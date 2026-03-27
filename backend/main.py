from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def home():
    return {"message": "StockSenseAI Backend Running "}

@app.post("/analyze")
def analyze():
    return {
        "decision": "BUY",
        "confidence": 0.82,
        "reasoning": "Strong market trend and positive signals",
        "risk": "MEDIUM"
    }
