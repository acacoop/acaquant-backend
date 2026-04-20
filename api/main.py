"""TradingAV API — FastAPI entrypoint.

Uso:
    uvicorn api.main:app --reload --port 8000

Auth:
    Todos los endpoints (excepto /api/health) requieren header
    Authorization: Bearer <API_KEY>.
    Si API_KEY no está definida en .env, auth está desactivada (modo dev).
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.gzip import GZipMiddleware

from api.deps import verify_api_key
from api.routers import (
    analitica,
    carteras,
    chat,
    cotizaciones,
    cuentas,
    manager,
    manager_resources,
    market,
    news,
    operaciones,
    titulos,
)
from core.mongo import get_mongo_client, get_mongo_client_read

logger = logging.getLogger("api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Pool warmup + sampler de recursos.

    Warmup: ping a Atlas al arrancar para que el primer request del día
    no pague la penalización de establecer conexión (~500ms–2s).

    Sampler: task background que toma snapshot de CPU/RAM/procesos cada
    60s para alimentar /api/manager/resources/history.
    """
    for nombre, getter in (("rw", get_mongo_client), ("read", get_mongo_client_read)):
        try:
            getter().admin.command("ping")
            logger.info("Mongo pool warmup OK (%s)", nombre)
        except Exception as e:
            logger.warning("Mongo pool warmup falló (%s): %s", nombre, e)

    sampler_task = asyncio.create_task(manager_resources.resources_sampler_loop(interval_s=60))
    try:
        yield
    finally:
        sampler_task.cancel()
        try:
            await sampler_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="TradingAV API", version="0.1.0", lifespan=lifespan)

# GZip: /historico/trades puede devolver hasta 10K trades JSON (~1-3 MB).
# Compresión ~80% en JSON. minimum_size=1024 evita overhead en responses chicas.
app.add_middleware(GZipMiddleware, minimum_size=1024)

app.include_router(analitica.router, dependencies=[Depends(verify_api_key)])
app.include_router(carteras.router, dependencies=[Depends(verify_api_key)])
app.include_router(cotizaciones.router, dependencies=[Depends(verify_api_key)])
app.include_router(cuentas.router, dependencies=[Depends(verify_api_key)])
app.include_router(operaciones.router, dependencies=[Depends(verify_api_key)])
app.include_router(titulos.router, dependencies=[Depends(verify_api_key)])
app.include_router(manager.router, dependencies=[Depends(verify_api_key)])
app.include_router(manager_resources.router, dependencies=[Depends(verify_api_key)])
app.include_router(chat.router, dependencies=[Depends(verify_api_key)])
app.include_router(news.router, dependencies=[Depends(verify_api_key)])
app.include_router(market.router, dependencies=[Depends(verify_api_key)])


@app.get("/api/health")
def health():
    return {"status": "ok"}
