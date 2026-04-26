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
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from api.auth import require_module
from api.deps import verify_api_key
from api.ratelimit import limiter
from api.routers import (
    analitica,
    carteras,
    chat,
    cotizaciones,
    cuentas,
    manager,
    manager_resources,
    market,
    me,
    news,
    operaciones,
    simulaciones,
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

# Rate limiter compartido — keying por email CF (ver api/ratelimit.py).
app.state.limiter = limiter


def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Handler custom: shape consistente con otros errores + status 429."""
    return JSONResponse(
        status_code=429,
        content={
            "detail": {
                "code": "rate_limit",
                "message": f"límite alcanzado: {exc.detail}",
                "retryable": True,
                "retry_after_s": 60,
            }
        },
    )


app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
app.add_middleware(SlowAPIMiddleware)

# GZip: /historico/trades puede devolver hasta 10K trades JSON (~1-3 MB).
# Compresión ~80% en JSON. minimum_size=1024 evita overhead en responses chicas.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# Dependencies por router (RBAC por módulo):
#   verify_api_key           → bearer token (común a todos).
#   require_module(modulo)   → gate por role según core/roles.py::MODULES.
#
# Cloudflare Access ya validó que el email puede entrar al sitio;
# require_module chequea que el role del email tenga el módulo listado
# en Manager.RoleMatrix.
_PUBLIC       = [Depends(verify_api_key)]
_PORTFOLIOS   = [Depends(verify_api_key), Depends(require_module("portfolios"))]
_OPERACIONES  = [Depends(verify_api_key), Depends(require_module("operaciones"))]
_ASISTENTE    = [Depends(verify_api_key), Depends(require_module("asistente"))]
_MANAGER      = [Depends(verify_api_key), Depends(require_module("manager"))]

# Públicos (todos los roles tienen home/renta-fija/derivados/estrategia):
app.include_router(me.router)                                      # /api/me — sin gate (identidad propia)
app.include_router(analitica.router,         dependencies=_PUBLIC)
app.include_router(cotizaciones.router,      dependencies=_PUBLIC)
app.include_router(news.router,              dependencies=_PUBLIC)
app.include_router(market.router,            dependencies=_PUBLIC)
app.include_router(simulaciones.router,      dependencies=_PUBLIC)  # gate por user_email en service

# Restringidos a roles con el módulo respectivo:
app.include_router(carteras.router,          dependencies=_PORTFOLIOS)
app.include_router(titulos.router,           dependencies=_PORTFOLIOS)
app.include_router(operaciones.router,       dependencies=_OPERACIONES)
app.include_router(cuentas.router,           dependencies=_OPERACIONES)
app.include_router(chat.router,              dependencies=_ASISTENTE)
app.include_router(manager.router,           dependencies=_MANAGER)
app.include_router(manager_resources.router, dependencies=_MANAGER)


@app.get("/api/health")
def health():
    return {"status": "ok"}
