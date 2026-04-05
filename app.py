from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from dotenv import load_dotenv
import anthropic, yfinance as yf, os, json, random, hashlib
from database import (init_db, SessionLocal, User, Task, Note, Event,
                      Holding, Transaction, Watchlist, QuizScore,
                      get_or_create_game_state)
from agents import master_agent
from datetime import datetime, timedelta
import urllib.request, re

load_dotenv()
init_db()

app = FastAPI(title="StockSense AI")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="static"), name="static")

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
JWT_SECRET = os.getenv("JWT_SECRET", "stocksense-secret-key-2024")
security = HTTPBearer(auto_error=False)

# ── JWT (pure Python, no pyjwt needed) ────────────────────────────────────────
import base64, hmac, time as _time

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _b64d(s: str) -> bytes:
    pad = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * pad)

def create_token(user_id: int, username: str) -> str:
    header = _b64(b'{"alg":"HS256","typ":"JWT"}')
    payload = _b64(json.dumps({"user_id": user_id, "username": username,
                                "exp": int(_time.time()) + 7*86400}).encode())
    sig = _b64(hmac.new(JWT_SECRET.encode(), f"{header}.{payload}".encode(), "sha256").digest())
    return f"{header}.{payload}.{sig}"

def decode_token(token: str) -> dict:
    try:
        parts = token.split(".")
        payload = json.loads(_b64d(parts[1]))
        if payload.get("exp", 0) < _time.time():
            raise ValueError("expired")
        header_payload = f"{parts[0]}.{parts[1]}"
        expected = _b64(hmac.new(JWT_SECRET.encode(), header_payload.encode(), "sha256").digest())
        if not hmac.compare_digest(expected, parts[2]):
            raise ValueError("invalid")
        return payload
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return decode_token(credentials.credentials)

def hash_pw(p: str) -> str:
    return hashlib.sha256(p.encode()).hexdigest()

# ── Market data ────────────────────────────────────────────────────────────────
STOCK_BASES = {
    "RELIANCE": 2840, "TCS": 3920, "INFY": 1760, "HDFC": 1690,
    "ICICI": 1120, "WIPRO": 485, "BAJAJ": 7250, "MARUTI": 11200,
    "TATAMOTORS": 965, "NIFTY": 22500, "SENSEX": 74200,
    "SUNPHARMA": 1580, "LTIM": 5400, "HCLTECH": 1650,
}
YAHOO_MAP = {
    "RELIANCE":"RELIANCE.NS","TCS":"TCS.NS","INFY":"INFY.NS","HDFC":"HDFCBANK.NS",
    "ICICI":"ICICIBANK.NS","WIPRO":"WIPRO.NS","BAJAJ":"BAJFINANCE.NS","MARUTI":"MARUTI.NS",
    "TATAMOTORS":"TATAMOTORS.NS","NIFTY":"^NSEI","SENSEX":"^BSESN",
    "SUNPHARMA":"SUNPHARMA.NS","LTIM":"LTIM.NS","HCLTECH":"HCLTECH.NS",
}

_price_cache: dict = {}
_cache_time: float = 0

def get_all_prices() -> dict:
    global _price_cache, _cache_time
    if _time.time() - _cache_time < 60 and _price_cache:
        return _price_cache
    result = {}
    for sym, base in STOCK_BASES.items():
        try:
            ticker = YAHOO_MAP.get(sym, sym)
            data = yf.Ticker(ticker).fast_info
            result[sym] = round(float(data.last_price), 2)
        except:
            result[sym] = round(base * (1 + random.uniform(-0.015, 0.015)), 2)
    _price_cache = result
    _cache_time = _time.time()
    return result

def get_price(sym: str) -> float:
    prices = get_all_prices()
    return prices.get(sym.upper(), STOCK_BASES.get(sym.upper(), 1000))

# ── News ───────────────────────────────────────────────────────────────────────
def fetch_news():
    try:
        url = "https://news.google.com/rss/search?q=indian+stock+market+NSE+BSE+NIFTY&hl=en-IN&gl=IN&ceid=IN:en"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            content = resp.read().decode("utf-8")
        titles = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", content)[1:8]
        if titles:
            return titles
    except:
        pass
    return [
        "NIFTY holds above 22,500 on strong FII inflows this week",
        "RBI keeps repo rate at 6.5% — what it means for equity markets",
        "Reliance Industries Q4 results beat analyst estimates; stock up 3%",
        "IT sector faces headwinds as US tech spending slows — TCS, Infosys impacted",
        "Bajaj Finance reports record quarterly profits; NBFC stocks rally",
        "Crude oil dips below $80 — positive signal for India's import bill",
        "SEBI introduces new F&O margin rules to protect retail investors",
    ]

# ── Root ───────────────────────────────────────────────────────────────────────
@app.get("/")
def root(): return FileResponse("static/index.html")

# ── Auth ───────────────────────────────────────────────────────────────────────
class AuthReq(BaseModel):
    username: str
    password: str
    email: str = ""

@app.post("/api/auth/signup")
def signup(req: AuthReq):
    db = SessionLocal()
    if db.query(User).filter(User.username == req.username).first():
        db.close(); raise HTTPException(400, "Username already taken")
    user = User(username=req.username, email=req.email or f"{req.username}@ss.ai",
                password_hash=hash_pw(req.password))
    db.add(user); db.commit(); db.refresh(user)
    get_or_create_game_state(db, user.id)
    token = create_token(user.id, user.username)
    db.close()
    return {"token": token, "username": user.username, "user_id": user.id, "xp": 0, "level": 1}

@app.post("/api/auth/login")
def login(req: AuthReq):
    db = SessionLocal()
    user = db.query(User).filter(User.username == req.username).first()
    if not user or user.password_hash != hash_pw(req.password):
        db.close(); raise HTTPException(401, "Invalid username or password")
    token = create_token(user.id, user.username)
    xp = user.xp; level = user.level
    db.close()
    return {"token": token, "username": user.username, "user_id": user.id, "xp": xp, "level": level}

# ── Market ─────────────────────────────────────────────────────────────────────
@app.get("/api/market")
def market(): return get_all_prices()

@app.get("/api/news")
def news(): return {"headlines": fetch_news()}

# ── Agent ──────────────────────────────────────────────────────────────────────
class AgentReq(BaseModel):
    message: str

@app.post("/api/agent")
def agent_ep(req: AgentReq, user=Depends(get_current_user)):
    mkt = get_all_prices()
    return master_agent(req.message, user["user_id"], mkt)

# ── Tasks ──────────────────────────────────────────────────────────────────────
class TaskReq(BaseModel):
    title: str

@app.get("/api/tasks")
def list_tasks(user=Depends(get_current_user)):
    db = SessionLocal()
    tasks = db.query(Task).filter(Task.user_id == user["user_id"]).order_by(Task.created_at.desc()).all()
    db.close()
    return [{"id": t.id, "title": t.title, "done": t.done, "date": t.created_at.strftime("%d %b")} for t in tasks]

@app.post("/api/tasks")
def create_task(req: TaskReq, user=Depends(get_current_user)):
    db = SessionLocal()
    t = Task(user_id=user["user_id"], title=req.title)
    db.add(t); db.commit(); db.refresh(t)
    db.close()
    return {"id": t.id, "title": t.title, "done": False}

@app.patch("/api/tasks/{tid}")
def toggle_task(tid: int, user=Depends(get_current_user)):
    db = SessionLocal()
    t = db.query(Task).filter(Task.id == tid, Task.user_id == user["user_id"]).first()
    if not t: raise HTTPException(404)
    t.done = not t.done; db.commit()
    db.close(); return {"id": t.id, "done": t.done}

@app.delete("/api/tasks/{tid}")
def del_task(tid: int, user=Depends(get_current_user)):
    db = SessionLocal()
    t = db.query(Task).filter(Task.id == tid, Task.user_id == user["user_id"]).first()
    if t: db.delete(t); db.commit()
    db.close(); return {"ok": True}

# ── Notes ──────────────────────────────────────────────────────────────────────
class NoteReq(BaseModel):
    content: str

@app.get("/api/notes")
def list_notes(user=Depends(get_current_user)):
    db = SessionLocal()
    notes = db.query(Note).filter(Note.user_id == user["user_id"]).order_by(Note.created_at.desc()).all()
    db.close()
    return [{"id": n.id, "content": n.content, "date": n.created_at.strftime("%d %b %Y")} for n in notes]

@app.post("/api/notes")
def create_note(req: NoteReq, user=Depends(get_current_user)):
    db = SessionLocal()
    n = Note(user_id=user["user_id"], content=req.content)
    db.add(n); db.commit(); db.refresh(n)
    db.close(); return {"id": n.id, "content": n.content}

@app.delete("/api/notes/{nid}")
def del_note(nid: int, user=Depends(get_current_user)):
    db = SessionLocal()
    n = db.query(Note).filter(Note.id == nid, Note.user_id == user["user_id"]).first()
    if n: db.delete(n); db.commit()
    db.close(); return {"ok": True}

# ── Events ─────────────────────────────────────────────────────────────────────
class EventReq(BaseModel):
    title: str; date: str; time: str = ""

@app.get("/api/events")
def list_events(user=Depends(get_current_user)):
    db = SessionLocal()
    events = db.query(Event).filter(Event.user_id == user["user_id"]).order_by(Event.date).all()
    db.close()
    return [{"id": e.id, "title": e.title, "date": e.date, "time": e.time} for e in events]

@app.post("/api/events")
def create_event(req: EventReq, user=Depends(get_current_user)):
    db = SessionLocal()
    e = Event(user_id=user["user_id"], title=req.title, date=req.date, time=req.time)
    db.add(e); db.commit(); db.refresh(e)
    db.close(); return {"id": e.id, "title": e.title, "date": e.date}

@app.delete("/api/events/{eid}")
def del_event(eid: int, user=Depends(get_current_user)):
    db = SessionLocal()
    e = db.query(Event).filter(Event.id == eid, Event.user_id == user["user_id"]).first()
    if e: db.delete(e); db.commit()
    db.close(); return {"ok": True}

# ── Portfolio & Trade ──────────────────────────────────────────────────────────
@app.get("/api/portfolio")
def portfolio(user=Depends(get_current_user)):
    db = SessionLocal()
    state = get_or_create_game_state(db, user["user_id"])
    holdings = db.query(Holding).filter(Holding.user_id == user["user_id"], Holding.quantity > 0).all()
    items = []
    total = state.cash
    for h in holdings:
        p = get_price(h.symbol)
        val = h.quantity * p
        pnl = (p - h.avg_price) * h.quantity
        total += val
        items.append({"symbol": h.symbol, "quantity": h.quantity, "avg_price": h.avg_price,
                      "current_price": p, "value": round(val, 2),
                      "pnl": round(pnl, 2), "pnl_pct": round((pnl / max(h.avg_price * h.quantity, 1)) * 100, 2)})
    db.close()
    return {"cash": round(state.cash, 2), "holdings": items, "total_value": round(total, 2)}

class TradeReq(BaseModel):
    symbol: str; action: str; amount: float

@app.post("/api/trade")
def trade(req: TradeReq, user=Depends(get_current_user)):
    db = SessionLocal()
    uid = user["user_id"]
    state = get_or_create_game_state(db, uid)
    price = get_price(req.symbol)
    sym = req.symbol.upper()
    if req.action == "buy":
        qty = req.amount / price
        if req.amount > state.cash:
            db.close(); return {"success": False, "message": f"Insufficient cash. Available: ₹{state.cash:,.0f}"}
        state.cash -= req.amount
        h = db.query(Holding).filter(Holding.user_id == uid, Holding.symbol == sym).first()
        if h:
            tq = h.quantity + qty
            h.avg_price = ((h.avg_price * h.quantity) + req.amount) / tq
            h.quantity = tq
        else:
            db.add(Holding(user_id=uid, symbol=sym, quantity=qty, avg_price=price))
        db.add(Transaction(user_id=uid, symbol=sym, action="buy", quantity=qty, price=price, total=req.amount))
        # Award XP for trading
        u = db.query(User).filter(User.id == uid).first()
        if u: u.xp += 5
        db.commit(); db.close()
        return {"success": True, "message": f"Bought {qty:.3f} shares of {sym} at ₹{price:,.2f} (+5 XP)"}
    elif req.action == "sell":
        h = db.query(Holding).filter(Holding.user_id == uid, Holding.symbol == sym).first()
        if not h or h.quantity <= 0:
            db.close(); return {"success": False, "message": f"No holdings in {sym}"}
        qty = min(req.amount / price, h.quantity)
        total = qty * price
        pnl = (price - h.avg_price) * qty
        state.cash += total
        h.quantity -= qty
        db.add(Transaction(user_id=uid, symbol=sym, action="sell", quantity=qty, price=price, total=total))
        u = db.query(User).filter(User.id == uid).first()
        if u:
            u.xp += 3
            if pnl > 0: u.xp += 10  # Bonus XP for profitable trade
        db.commit(); db.close()
        bonus = " (+10 XP profit bonus!)" if pnl > 0 else ""
        return {"success": True, "message": f"Sold {qty:.3f} {sym} for ₹{total:,.2f}{bonus}"}
    db.close(); return {"success": False, "message": "Invalid action"}

@app.get("/api/history")
def history(user=Depends(get_current_user)):
    db = SessionLocal()
    txns = db.query(Transaction).filter(Transaction.user_id == user["user_id"]).order_by(Transaction.timestamp.desc()).limit(20).all()
    db.close()
    return [{"symbol": t.symbol, "action": t.action, "quantity": round(t.quantity, 3),
             "price": t.price, "total": t.total, "timestamp": t.timestamp.strftime("%d %b, %I:%M %p")} for t in txns]

# ── Watchlist ──────────────────────────────────────────────────────────────────
class WatchReq(BaseModel):
    symbol: str; target_price: float; alert_type: str

@app.post("/api/watchlist")
def add_watch(req: WatchReq, user=Depends(get_current_user)):
    db = SessionLocal()
    uid = user["user_id"]; sym = req.symbol.upper()
    ex = db.query(Watchlist).filter(Watchlist.user_id == uid, Watchlist.symbol == sym).first()
    if ex:
        ex.target_price = req.target_price; ex.alert_type = req.alert_type
    else:
        db.add(Watchlist(user_id=uid, symbol=sym, target_price=req.target_price, alert_type=req.alert_type))
    db.commit(); db.close()
    return {"success": True, "message": f"{sym} alert set at ₹{req.target_price:,.0f}"}

@app.get("/api/watchlist")
def get_watch(user=Depends(get_current_user)):
    db = SessionLocal()
    items = db.query(Watchlist).filter(Watchlist.user_id == user["user_id"]).all()
    result = []
    for w in items:
        p = get_price(w.symbol)
        triggered = (w.alert_type == "above" and p >= w.target_price) or (w.alert_type == "below" and p <= w.target_price)
        diff_pct = round(((p - w.target_price) / w.target_price) * 100, 2)
        result.append({"symbol": w.symbol, "current_price": p, "target_price": w.target_price,
                       "alert_type": w.alert_type, "triggered": triggered, "diff_pct": diff_pct})
    db.close(); return result

@app.delete("/api/watchlist/{sym}")
def del_watch(sym: str, user=Depends(get_current_user)):
    db = SessionLocal()
    w = db.query(Watchlist).filter(Watchlist.user_id == user["user_id"], Watchlist.symbol == sym.upper()).first()
    if w: db.delete(w); db.commit()
    db.close(); return {"ok": True}

# ── Game ───────────────────────────────────────────────────────────────────────
class QuizRes(BaseModel):
    category: str; score: int; total: int; xp_earned: int

@app.post("/api/game/quiz")
def save_quiz(req: QuizRes, user=Depends(get_current_user)):
    db = SessionLocal()
    uid = user["user_id"]
    db.add(QuizScore(user_id=uid, category=req.category, score=req.score, total=req.total))
    u = db.query(User).filter(User.id == uid).first()
    if u:
        u.xp += req.xp_earned
        u.level = max(1, u.xp // 100 + 1)
    db.commit(); db.close()
    return {"success": True, "new_xp": u.xp, "new_level": u.level}

@app.get("/api/game/stats")
def game_stats(user=Depends(get_current_user)):
    db = SessionLocal()
    uid = user["user_id"]
    u = db.query(User).filter(User.id == uid).first()
    scores = db.query(QuizScore).filter(QuizScore.user_id == uid).order_by(QuizScore.played_at.desc()).limit(5).all()
    db.close()
    return {"xp": u.xp, "level": u.level,
            "recent": [{"cat": s.category, "score": s.score, "total": s.total} for s in scores]}

# ── WebSocket: News AI ─────────────────────────────────────────────────────────
@app.websocket("/ws/news")
async def ws_news(ws: WebSocket):
    await ws.accept()
    try:
        raw = await ws.receive_text()
        data = json.loads(raw)
        headline = data.get("headline", "")
        mkt = get_all_prices()
        prompt = f"""You are StockSense AI — a sharp, friendly Indian financial analyst.

A user wants to understand this news: "{headline}"

Live market context: NIFTY ₹{mkt.get('NIFTY','N/A')} | SENSEX ₹{mkt.get('SENSEX','N/A')}

Reply in exactly 3 short sections with bold headers:
**Kya hua?** — 1-2 lines explaining what happened (Hinglish, very simple)
**Kaun se stocks?** — Which specific stocks/sectors are affected and how
**Aapke liye?** — One practical takeaway for a retail investor

Keep total under 100 words. Friendly, direct tone."""

        with client.messages.stream(
            model="claude-sonnet-4-20250514", max_tokens=350,
            messages=[{"role": "user", "content": prompt}]
        ) as stream:
            for text in stream.text_stream:
                await ws.send_text(json.dumps({"type": "chunk", "text": text}))
        await ws.send_text(json.dumps({"type": "done"}))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try: await ws.send_text(json.dumps({"type": "error", "text": str(e)}))
        except: pass

# ── WebSocket: Main Chat (FIXED) ───────────────────────────────────────────────
@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    await ws.accept()
    user_id = None
    username = "Guest"
    system_prompt = ""

    try:
        # Step 1: Receive auth token
        raw = await ws.receive_text()
        init = json.loads(raw)
        token_str = init.get("token", "")

        try:
            payload = decode_token(token_str)
            user_id = payload["user_id"]
            username = payload["username"]
        except:
            user_id = 0
            username = "Guest"

        # Step 2: Build system prompt with live market data
        mkt = get_all_prices()
        portfolio_summary = "No holdings"
        if user_id:
            db = SessionLocal()
            try:
                state = get_or_create_game_state(db, user_id)
                holdings = db.query(Holding).filter(Holding.user_id == user_id, Holding.quantity > 0).all()
                if holdings:
                    portfolio_summary = f"Cash ₹{state.cash:,.0f} | Holdings: {', '.join([h.symbol for h in holdings])}"
                else:
                    portfolio_summary = f"Cash ₹{state.cash:,.0f} | No stock positions yet"
            finally:
                db.close()

        system_prompt = f"""You are StockSense AI — India's most knowledgeable stock market assistant. You are a friendly expert who speaks in clear, warm Hinglish (natural mix of Hindi and English).

=== LIVE MARKET DATA ===
NIFTY 50: ₹{mkt.get('NIFTY', 'N/A')}
SENSEX: ₹{mkt.get('SENSEX', 'N/A')}
RELIANCE: ₹{mkt.get('RELIANCE', 'N/A')} | TCS: ₹{mkt.get('TCS', 'N/A')} | INFY: ₹{mkt.get('INFY', 'N/A')}
HDFC Bank: ₹{mkt.get('HDFC', 'N/A')} | ICICI: ₹{mkt.get('ICICI', 'N/A')} | WIPRO: ₹{mkt.get('WIPRO', 'N/A')}
Bajaj Finance: ₹{mkt.get('BAJAJ', 'N/A')} | Maruti: ₹{mkt.get('MARUTI', 'N/A')}

=== USER ===
Name: {username}
Portfolio: {portfolio_summary}

=== YOUR EXPERTISE ===
- NSE/BSE stocks, NIFTY, SENSEX, indices
- Mutual funds, ETFs, F&O, IPOs, bonds, SIPs
- Technical analysis: RSI, MACD, moving averages, candlesticks, Bollinger Bands
- Fundamental analysis: P/E, EPS, ROE, debt ratios, promoter holding, cash flow
- Macro: RBI policy, inflation, FII/DII flows, crude oil, USD/INR
- Tax: LTCG, STCG, STT, dividend taxation
- Risk management, portfolio allocation, SIP strategies

=== STYLE ===
- Warm, like a knowledgeable dost (friend), not a formal banker
- Use ₹ for all Indian amounts
- Give concrete, specific answers — not vague
- Always add: "Ye financial advice nahi hai, apna research zaroor karo"
- Keep responses focused — 2-3 paragraphs max unless user asks for detail
- If asked about user's portfolio, use the data above"""

        # Step 3: Send ready signal
        await ws.send_text(json.dumps({"type": "ready"}))

        # Step 4: Message loop
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            user_msg = msg.get("message", "").strip()
            history = msg.get("history", [])

            if not user_msg:
                continue

            messages = history[-10:] + [{"role": "user", "content": user_msg}]

            try:
                with client.messages.stream(
                    model="claude-sonnet-4-20250514",
                    max_tokens=1000,
                    system=system_prompt,
                    messages=messages
                ) as stream:
                    for text in stream.text_stream:
                        await ws.send_text(json.dumps({"type": "chunk", "text": text}))
                await ws.send_text(json.dumps({"type": "done"}))
            except Exception as e:
                await ws.send_text(json.dumps({"type": "error", "text": f"AI error: {str(e)}"}))

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try: await ws.send_text(json.dumps({"type": "error", "text": str(e)}))
        except: pass
