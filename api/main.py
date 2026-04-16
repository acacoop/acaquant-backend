"""TradingAV API — FastAPI entrypoint.

Uso:
    uvicorn api.main:app --reload --port 8000
"""
from fastapi import FastAPI

from api.routers import cuentas

app = FastAPI(title="TradingAV API", version="0.1.0")

app.include_router(cuentas.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
