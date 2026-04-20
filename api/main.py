"""TradingAV API — FastAPI entrypoint.

Uso:
    uvicorn api.main:app --reload --port 8000

Auth:
    Todos los endpoints (excepto /api/health) requieren header
    Authorization: Bearer <API_KEY>.
    Si API_KEY no está definida en .env, auth está desactivada (modo dev).
"""
from fastapi import Depends, FastAPI

from api.deps import verify_api_key
from api.routers import (
    carteras,
    chat,
    cotizaciones,
    cuentas,
    manager,
    market,
    news,
    operaciones,
    titulos,
)

app = FastAPI(title="TradingAV API", version="0.1.0")

app.include_router(carteras.router, dependencies=[Depends(verify_api_key)])
app.include_router(cotizaciones.router, dependencies=[Depends(verify_api_key)])
app.include_router(cuentas.router, dependencies=[Depends(verify_api_key)])
app.include_router(operaciones.router, dependencies=[Depends(verify_api_key)])
app.include_router(titulos.router, dependencies=[Depends(verify_api_key)])
app.include_router(manager.router, dependencies=[Depends(verify_api_key)])
app.include_router(chat.router, dependencies=[Depends(verify_api_key)])
app.include_router(news.router, dependencies=[Depends(verify_api_key)])
app.include_router(market.router, dependencies=[Depends(verify_api_key)])


@app.get("/api/health")
def health():
    return {"status": "ok"}
