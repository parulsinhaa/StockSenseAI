"""
Multi-Agent System — StockSense AI
Master Agent routes intent to specialized sub-agents (MCP-style tool execution).
"""
import json
from datetime import datetime
from database import SessionLocal, Task, Note, Event, get_or_create_game_state, Holding

TOOLS = {
    "task_agent":     ["create_task", "list_tasks", "complete_task", "delete_task"],
    "notes_agent":    ["create_note", "list_notes", "search_notes"],
    "calendar_agent": ["create_event", "list_events", "delete_event"],
    "finance_agent":  ["get_portfolio", "get_balance", "get_trade_history"],
    "market_agent":   ["get_price", "get_watchlist", "get_market_overview"],
}

INTENT_MAP = {
    "task": "task_agent", "todo": "task_agent", "remind": "task_agent", "add task": "task_agent",
    "note": "notes_agent", "write": "notes_agent", "save note": "notes_agent",
    "calendar": "calendar_agent", "schedule": "calendar_agent", "event": "calendar_agent",
    "portfolio": "finance_agent", "balance": "finance_agent", "holding": "finance_agent",
    "price": "market_agent", "market": "market_agent", "stock": "market_agent",
    "nifty": "market_agent", "sensex": "market_agent",
}

def master_agent(user_message: str, user_id: int, market_data: dict) -> dict:
    lower = user_message.lower()
    agent = "market_agent"
    for keyword, mapped in INTENT_MAP.items():
        if keyword in lower:
            agent = mapped
            break
    result = dispatch_agent(agent, user_message, user_id, market_data)
    return {"routed_to": agent, "tools_available": TOOLS.get(agent, []),
            "result": result, "timestamp": datetime.utcnow().isoformat()}

def dispatch_agent(agent, message, user_id, market_data):
    if agent == "task_agent":     return task_agent(message, user_id)
    if agent == "notes_agent":    return notes_agent(message, user_id)
    if agent == "calendar_agent": return calendar_agent(message, user_id)
    if agent == "finance_agent":  return finance_agent(user_id, market_data)
    return market_agent_fn(market_data)

def task_agent(message, user_id):
    db = SessionLocal()
    lower = message.lower()
    try:
        if any(w in lower for w in ["create", "add", "new"]):
            title = message.strip()
            for p in ["create task", "add task", "new task", "create", "add"]:
                title = title.lower().replace(p, "").strip()
            title = title.capitalize() or "New task"
            t = Task(user_id=user_id, title=title)
            db.add(t); db.commit()
            return {"action": "create_task", "status": "success", "task": title}
        elif any(w in lower for w in ["done", "complete"]):
            tasks = db.query(Task).filter(Task.user_id == user_id, Task.done == False).all()
            for t in tasks: t.done = True
            db.commit()
            return {"action": "complete_all", "count": len(tasks)}
        else:
            tasks = db.query(Task).filter(Task.user_id == user_id).order_by(Task.created_at.desc()).limit(10).all()
            return {"action": "list_tasks", "tasks": [{"id": t.id, "title": t.title, "done": t.done} for t in tasks]}
    finally:
        db.close()

def notes_agent(message, user_id):
    db = SessionLocal()
    lower = message.lower()
    try:
        if any(w in lower for w in ["save", "note", "write"]):
            content = message.strip()
            for p in ["save note", "write note", "note:", "note", "save", "write"]:
                content = content.lower().replace(p, "").strip()
            content = content or message
            n = Note(user_id=user_id, content=content)
            db.add(n); db.commit()
            return {"action": "create_note", "status": "success", "preview": content[:60]}
        else:
            notes = db.query(Note).filter(Note.user_id == user_id).order_by(Note.created_at.desc()).limit(5).all()
            return {"action": "list_notes", "notes": [{"id": n.id, "content": n.content[:80]} for n in notes]}
    finally:
        db.close()

def calendar_agent(message, user_id):
    db = SessionLocal()
    lower = message.lower()
    try:
        if any(w in lower for w in ["schedule", "add", "create", "remind"]):
            title = message.strip()
            for p in ["schedule", "add event", "create event", "remind me"]:
                title = title.lower().replace(p, "").strip()
            title = title.capitalize() or "New event"
            today = datetime.utcnow().strftime("%Y-%m-%d")
            e = Event(user_id=user_id, title=title, date=today)
            db.add(e); db.commit()
            return {"action": "create_event", "status": "success", "event": title, "date": today}
        else:
            events = db.query(Event).filter(Event.user_id == user_id).order_by(Event.date).limit(10).all()
            return {"action": "list_events", "events": [{"id": e.id, "title": e.title, "date": e.date} for e in events]}
    finally:
        db.close()

def finance_agent(user_id, market_data):
    db = SessionLocal()
    try:
        state = get_or_create_game_state(db, user_id)
        holdings = db.query(Holding).filter(Holding.user_id == user_id, Holding.quantity > 0).all()
        portfolio = []
        total = state.cash
        for h in holdings:
            price = market_data.get(h.symbol, h.avg_price)
            val = h.quantity * price
            total += val
            portfolio.append({"symbol": h.symbol, "qty": round(h.quantity, 2),
                              "value": round(val, 2), "pnl": round((price - h.avg_price) * h.quantity, 2)})
        return {"action": "get_portfolio", "cash": round(state.cash, 2),
                "total_value": round(total, 2), "holdings": portfolio}
    finally:
        db.close()

def market_agent_fn(market_data):
    return {"action": "get_market_overview",
            "nifty": market_data.get("NIFTY", 0),
            "sensex": market_data.get("SENSEX", 0),
            "top_stocks": {k: v for k, v in market_data.items() if k not in ["NIFTY", "SENSEX"]}}
