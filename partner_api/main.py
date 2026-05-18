"""partner_api — app FastAPI del servicio externo de datos para el proveedor.

Arrancar en dev:
    uvicorn partner_api.main:app --port 8100

En el Droplet corre como systemd service (deploy/partner_api.service),
bindeado a 127.0.0.1:8100 y expuesto vía nginx en data.acaquant.com.

Endpoints:
    POST /v1/token       login usuario/password → JWT
    GET  /v1/fechas      fechas disponibles    (requiere token)
    GET  /v1/portfolio   posiciones            (requiere token)
    GET  /health         liveness, sin auth
"""
from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from partner_api import auth, routes
from partner_api.ratelimit import client_ip, limiter
from partner_api.security import validar_token
from partner_api.settings import PARTNER_JWT_SECRET, PARTNER_MONGO_URI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("partner_api")
_audit = logging.getLogger("partner_api.audit")

# Chequeo de config al importar — si faltan las env vars, queda logueado
# fuerte en el arranque del servicio.
_faltan = [
    n for n, v in (("PARTNER_MONGO_URI", PARTNER_MONGO_URI),
                   ("PARTNER_JWT_SECRET", PARTNER_JWT_SECRET))
    if not v
]
if _faltan:
    logger.error("FALTAN env vars %s — el servicio no va a funcionar.", _faltan)

app = FastAPI(
    title="Acaquant Partner API",
    version="1.0.0",
    # Sin Swagger / OpenAPI público: no le damos el esquema a internet.
    # El proveedor recibe la doc por escrito (docs/PARTNER_API.md).
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(auth.router)
app.include_router(routes.router)


@app.middleware("http")
async def _audit_middleware(request: Request, call_next):
    """Loguea cada request — IP, usuario, método, path, status, latencia."""
    inicio = time.time()
    response = await call_next(request)
    dur_ms = (time.time() - inicio) * 1000
    # username best-effort desde el Bearer token (sin tocar la DB).
    usuario = "-"
    authz = request.headers.get("authorization", "")
    if authz.lower().startswith("bearer "):
        usuario = validar_token(authz[7:]) or "?"
    _audit.info(
        "ip=%s user=%s %s %s -> %s %.0fms",
        client_ip(request), usuario, request.method,
        request.url.path, response.status_code, dur_ms,
    )
    return response


@app.get("/health")
def health() -> dict:
    """Liveness check — sin auth. Para monitoreo o el proveedor."""
    return {"status": "ok"}


@app.get("/")
def root() -> dict:
    return {"service": "acaquant-partner-api", "version": "1.0.0"}
