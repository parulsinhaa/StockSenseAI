from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

engine = create_engine("sqlite:///stocksense.db", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True)
    password_hash = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    xp = Column(Integer, default=0)
    level = Column(Integer, default=1)

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    title = Column(String)
    done = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Note(Base):
    __tablename__ = "notes"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    title = Column(String)
    date = Column(String)
    time = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class GameState(Base):
    __tablename__ = "game_state"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)
    cash = Column(Float, default=100000.0)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Holding(Base):
    __tablename__ = "holdings"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    symbol = Column(String)
    quantity = Column(Float, default=0)
    avg_price = Column(Float, default=0)

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    symbol = Column(String)
    action = Column(String)
    quantity = Column(Float)
    price = Column(Float)
    total = Column(Float)
    timestamp = Column(DateTime, default=datetime.utcnow)

class Watchlist(Base):
    __tablename__ = "watchlist"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    symbol = Column(String)
    target_price = Column(Float)
    alert_type = Column(String, default="above")
    added_at = Column(DateTime, default=datetime.utcnow)

class QuizScore(Base):
    __tablename__ = "quiz_scores"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    category = Column(String)
    score = Column(Integer, default=0)
    total = Column(Integer, default=0)
    played_at = Column(DateTime, default=datetime.utcnow)

def init_db():
    Base.metadata.create_all(bind=engine)

def get_or_create_game_state(db, user_id: int):
    state = db.query(GameState).filter(GameState.user_id == user_id).first()
    if not state:
        state = GameState(user_id=user_id, cash=100000.0)
        db.add(state)
        db.commit()
        db.refresh(state)
    return state
