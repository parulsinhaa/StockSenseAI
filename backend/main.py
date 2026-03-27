from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def home():
    return {"message": "StockSenseAI Backend Running "}

@app.get("/test")
def test():
    return {"status": "working"}
