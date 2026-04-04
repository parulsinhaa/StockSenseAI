from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import anthropic, yfinance as yf, os, json, random
from database import init_db, SessionLocal, GameState, Holding, Transaction, Note, Watchlist
from datetime import datetime
import urllib.request

load_dotenv()
init_db()

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="static"), name="static")

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

INDIAN_STOCKS = {
    "RELIANCE": "RELIANCE.NS", "TCS": "TCS.NS", "INFY": "INFY.NS",
    "HDFC": "HDFCBANK.NS", "ICICI": "ICICIBANK.NS", "WIPRO": "WIPRO.NS",
    "BAJAJ": "BAJFINANCE.NS", "MARUTI": "MARUTI.NS", "TATAMOTORS": "TATAMOTORS.NS",
    "NIFTY": "^NSEI", "SENSEX": "^BSESN"
}

def get_price(symbol: str) -> float:
    ticker = INDIAN_STOCKS.get(symbol.upper(), symbol)
    try:
        data = yf.Ticker(ticker).fast_info
        return round(data.last_price, 2)
    except:
        base = {"RELIANCE": 2800, "TCS": 3900, "INFY": 1750, "HDFC": 1680,
                "ICICI": 1100, "WIPRO": 480, "BAJAJ": 7200, "MARUTI": 11000,
                "TATAMOTORS": 950, "NIFTY": 22500, "SENSEX": 74000}
        b = base.get(symbol.upper(), 1000)
        return round(b * (1 + random.uniform(-0.02, 0.02)), 2)

def fetch_news() -> list:
    try:
        url = "https://news.google.com/rss/search?q=indian+stock+market+NIFTY+SENSEX&hl=en-IN&gl=IN&ceid=IN:en"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            content = resp.read().decode("utf-8")
        import re
        titles = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", content)[1:8]
        if titles:
            return titles
    except:
        pass
    return [
        "NIFTY crosses 22,500 amid strong FII buying",
        "RBI holds repo rate steady at 6.5%, market reacts positively",
        "Reliance Industries Q4 results beat estimates, stock surges",
        "IT sector under pressure as US recession fears mount",
        "Bajaj Finance posts record profits, NBFC stocks rally",
        "Crude oil prices dip — good news for Indian markets",
        "SEBI introduces new F&O regulations to protect retail investors"
    ]

@app.get("/")
def root():
    return FileResponse("static/index.html")

@app.get("/api/market")
def market_data():
    result = {}
    for sym in ["NIFTY", "SENSEX", "RELIANCE", "TCS", "INFY", "HDFC", "ICICI", "WIPRO"]:
        result[sym] = get_price(sym)
    return result

@app.get("/api/portfolio")
def get_portfolio():
    db = SessionLocal()
    state = db.query(GameState).first()
    holdings = db.query(Holding).filter(Holding.quantity > 0).all()
    portfolio = []
    total_value = state.cash
    for h in holdings:
        price = get_price(h.symbol)
        value = h.quantity * price
        pnl = (price - h.avg_price) * h.quantity
        pnl_pct = ((price - h.avg_price) / h.avg_price) * 100
        total_value += value
        portfolio.append({
            "symbol": h.symbol, "quantity": h.quantity,
            "avg_price": h.avg_price, "current_price": price,
            "value": round(value, 2), "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2)
        })
    db.close()
    return {"cash": round(state.cash, 2), "holdings": portfolio, "total_value": round(total_value, 2)}

class TradeRequest(BaseModel):
    symbol: str
    action: str
    amount: float

@app.post("/api/trade")
def trade(req: TradeRequest):
    db = SessionLocal()
    state = db.query(GameState).first()
    price = get_price(req.symbol)
    symbol = req.symbol.upper()

    if req.action == "buy":
        qty = req.amount / price
        if req.amount > state.cash:
            db.close()
            return {"success": False, "message": f"Insufficient cash. You have ₹{state.cash:,.0f}"}
        state.cash -= req.amount
        holding = db.query(Holding).filter(Holding.symbol == symbol).first()
        if holding:
            total_qty = holding.quantity + qty
            holding.avg_price = ((holding.avg_price * holding.quantity) + req.amount) / total_qty
            holding.quantity = total_qty
        else:
            db.add(Holding(symbol=symbol, quantity=qty, avg_price=price))
        db.add(Transaction(symbol=symbol, action="buy", quantity=qty, price=price, total=req.amount))
        db.commit(); db.close()
        return {"success": True, "message": f"Bought {qty:.2f} shares of {symbol} at ₹{price:,.2f}"}

    elif req.action == "sell":
        holding = db.query(Holding).filter(Holding.symbol == symbol).first()
        if not holding or holding.quantity <= 0:
            db.close()
            return {"success": False, "message": f"No holdings in {symbol}"}
        qty = req.amount / price
        if qty > holding.quantity:
            qty = holding.quantity
        total = qty * price
        state.cash += total
        holding.quantity -= qty
        db.add(Transaction(symbol=symbol, action="sell", quantity=qty, price=price, total=total))
        db.commit(); db.close()
        return {"success": True, "message": f"Sold {qty:.2f} shares of {symbol} for ₹{total:,.2f}"}

    db.close()
    return {"success": False, "message": "Invalid action"}

@app.get("/api/history")
def trade_history():
    db = SessionLocal()
    txns = db.query(Transaction).order_by(Transaction.timestamp.desc()).limit(20).all()
    db.close()
    return [{"symbol": t.symbol, "action": t.action, "quantity": round(t.quantity, 3),
             "price": t.price, "total": t.total,
             "timestamp": t.timestamp.strftime("%d %b %Y, %I:%M %p")} for t in txns]

# ── Watchlist ──────────────────────────────────────────────────────────────────

class WatchlistRequest(BaseModel):
    symbol: str
    target_price: float
    alert_type: str

@app.post("/api/watchlist")
def add_watchlist(req: WatchlistRequest):
    db = SessionLocal()
    symbol = req.symbol.upper()
    existing = db.query(Watchlist).filter(Watchlist.symbol == symbol).first()
    if existing:
        existing.target_price = req.target_price
        existing.alert_type = req.alert_type
    else:
        db.add(Watchlist(symbol=symbol, target_price=req.target_price, alert_type=req.alert_type))
    db.commit(); db.close()
    return {"success": True, "message": f"{symbol} added to watchlist"}

@app.delete("/api/watchlist/{symbol}")
def remove_watchlist(symbol: str):
    db = SessionLocal()
    item = db.query(Watchlist).filter(Watchlist.symbol == symbol.upper()).first()
    if item:
        db.delete(item)
        db.commit()
    db.close()
    return {"success": True}

@app.get("/api/watchlist")
def get_watchlist():
    db = SessionLocal()
    items = db.query(Watchlist).all()
    result = []
    for w in items:
        price = get_price(w.symbol)
        triggered = (w.alert_type == "above" and price >= w.target_price) or \
                    (w.alert_type == "below" and price <= w.target_price)
        result.append({
            "symbol": w.symbol,
            "current_price": price,
            "target_price": w.target_price,
            "alert_type": w.alert_type,
            "triggered": triggered
        })
    db.close()
    return result

# ── News ───────────────────────────────────────────────────────────────────────

@app.get("/api/news")
def get_news():
    return {"headlines": fetch_news()}

@app.websocket("/ws/news")
async def news_ws(ws: WebSocket):
    await ws.accept()
    try:
        data = await ws.receive_text()
        payload = json.loads(data)
        headline = payload.get("headline", "")
        market = market_data()

        prompt = f"""You are StockSense AI, a friendly Indian financial assistant.

A user clicked on this news headline to understand it:
"{headline}"

Current market:
- NIFTY: ₹{market.get('NIFTY','N/A')}
- SENSEX: ₹{market.get('SENSEX','N/A')}

Explain in exactly 3 short sections using these headings:

**Kya hua?**
(1-2 sentences — what happened, plain Hinglish)

**Market pe asar?**
(Which stocks or sectors are affected. Be specific — name them.)

**Aapke liye matlab?**
(One practical takeaway for a retail investor. Not financial advice, just context.)

Keep total response under 120 words. Friendly tone."""

        with client.messages.stream(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        ) as stream:
            for text in stream.text_stream:
                await ws.send_text(json.dumps({"type": "chunk", "text": text}))
        await ws.send_text(json.dumps({"type": "done"}))
    except WebSocketDisconnect:
        pass

# ── Main Chat ──────────────────────────────────────────────────────────────────

@app.websocket("/ws/chat")
async def chat_ws(ws: WebSocket):
    await ws.accept()
    portfolio = get_portfolio()
    market = market_data()
    system_prompt = f"""You are StockSense AI, a smart financial assistant for Indian retail investors.

Current Market:
- NIFTY: ₹{market.get('NIFTY', 'N/A')}
- SENSEX: ₹{market.get('SENSEX', 'N/A')}
- RELIANCE: ₹{market.get('RELIANCE', 'N/A')}
- TCS: ₹{market.get('TCS', 'N/A')}
- INFY: ₹{market.get('INFY', 'N/A')}

User Portfolio:
- Cash: ₹{portfolio['cash']:,.0f}
- Total Value: ₹{portfolio['total_value']:,.0f}
- Holdings: {json.dumps(portfolio['holdings'])}

You help users understand markets, analyse stocks, explain financial concepts in simple Hindi-English (Hinglish is fine). Be concise, friendly, practical. Use ₹ for amounts. Never give guaranteed profit advice."""

    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)
            user_msg = msg.get("message", "")
            history = msg.get("history", [])
            messages = history[-10:] + [{"role": "user", "content": user_msg}]
            with client.messages.stream(
                model="claude-sonnet-4-20250514",
                max_tokens=1000,
                system=system_prompt,
                messages=messages
            ) as stream:
                for text in stream.text_stream:
                    await ws.send_text(json.dumps({"type": "chunk", "text": text}))
            await ws.send_text(json.dumps({"type": "done"}))
    except WebSocketDisconnect:
        pass
