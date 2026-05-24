import os
from fastapi import FastAPI

app = FastAPI(title="Concurrent Bidding System")

@app.get("/")
def read_root():
    return {"message": "Welcome to the Concurrent Bidding System"}
