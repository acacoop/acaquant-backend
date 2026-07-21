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
from fastapi.responses import ORJSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from api.auth import require_any_module, require_module
from api.deps import verify_api_key
from api.profiling import maybe_add_profiler
from api.ratelimit import limiter
from api.routers import (
    analitica,
    asistente,
    back_office,
    calendario,
    carteras,
    cotizaciones,
    cuentas,
    derivados_agro,
    derivados_sinteticos,
    ia,
    ingest,
    manager,
    manager_resources,
    market,
    me,
    news,
    operaciones,
    operar,
    operativa,
    ordenes,
    research,
    research1816,
    research_bcra,
    research_docs,
    research_fred,
    risk,
    scanner,
    titulos,
    trading,
    valuaciones,
)
from config import (
    API_KEY,
    CF_ACCESS_AUD,
    CF_ACCESS_TEAM,
    ENV,
    MCP_BEARER_TOKEN,
    MCP_JWT_SECRET,
)

logger = logging.getLogger("api")


def _validar_postura_auth() -> None:
    """Chequea la config de auth al boot (EXT-AUTH1).

    Fail-closed en prod: si `ENV=prod` y falta `API_KEY` **o** falta el par
    `CF_ACCESS_TEAM`/`CF_ACCESS_AUD`, abortamos el arranque — preferimos una
    caída ruidosa (systemd `failed`, 502 visible) a que la API quede abierta
    en silencio. Sin CF_ACCESS_TEAM/AUD, `get_user_email` no valida el JWT de
    Cloudflare y cae al header forwardeado (spoofeable) → todo el RBAC queda
    de adorno. En dev solo logueamos un warning. Siempre dejamos un log con
    las capas de auth activas para poder auditar la postura sin adivinar.

    Antes de deployar esto: `python -m scripts.diag_auth_postura` en el
    Droplet confirma que las env vars están en el unit file.
    """
    capas = []
    if API_KEY:
        capas.append("API_KEY")
    if CF_ACCESS_TEAM and CF_ACCESS_AUD:
        capas.append("CF-JWT")

    if ENV == "prod":
        if not API_KEY:
            raise RuntimeError(
                "EXT-AUTH1: ENV=prod pero API_KEY está vacía → fail-closed. "
                "Configurá API_KEY en el unit de systemd (o poné ENV=dev si es local)."
            )
        if not (CF_ACCESS_TEAM and CF_ACCESS_AUD):
            faltan = [
                n for n, v in (("CF_ACCESS_TEAM", CF_ACCESS_TEAM), ("CF_ACCESS_AUD", CF_ACCESS_AUD))
                if not v
            ]
            raise RuntimeError(
                f"EXT-AUTH1: ENV=prod sin {'/'.join(faltan)} → fail-closed. Sin esas dos "
                "variables el JWT de CF NO se valida criptográficamente y la identidad cae "
                "al header forwardeado (spoofeable) → el RBAC no protege nada. Configuralas "
                "en el unit de systemd (o poné ENV=dev si es local). "
                "Chequeo previo: python -m scripts.diag_auth_postura"
            )
    elif not API_KEY:
        logger.warning(
            "auth: API_KEY vacía (ENV=%s) — la API acepta requests sin bearer. "
            "OK en dev; en prod seteá API_KEY y ENV=prod.", ENV,
        )

    logger.info("auth posture: capas=%s ENV=%s", capas or ["NINGUNA"], ENV)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Sampler de recursos.

    Sampler: task background que toma snapshot de CPU/RAM/procesos cada
    60s para alimentar /api/manager/resources/history.
    """
    _validar_postura_auth()

    sampler_task = asyncio.create_task(manager_resources.resources_sampler_loop(interval_s=60))

    background_tasks = [sampler_task]

    async def _shutdown_bg():
        for t in background_tasks:
            t.cancel()
        for t in background_tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass

    # Si MCP está configurado (con token estático o JWT secret), levantamos
    # su session manager dentro del mismo lifespan. Si nada está
    # configurado, no se monta y se saltea.
    if MCP_BEARER_TOKEN or MCP_JWT_SECRET:
        from api.mcp.server import mcp as mcp_server
        async with mcp_server.session_manager.run():
            try:
                yield
            finally:
                await _shutdown_bg()
    else:
        try:
            yield
        finally:
            await _shutdown_bg()


app = FastAPI(
    title="TradingAV API",
    version="0.1.0",
    lifespan=lifespan,
    # orjson es 2-3x más rápido que el stdlib json en payloads grandes
    # (snapshots de portfolios, listas de boletos, series de TimeSales)
    # y aloca proporcionalmente menos RAM durante la serialización.
    default_response_class=ORJSONResponse,
)

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


# Telemetría de uso por módulo (manager.uso_modulos — docs/OBSERVABILIDAD_ROBUSTEZ.md).
# POST-response y solo memoria (el flush a SQL corre en thread cada ~60s): cero
# I/O en el hot path, y un fallo acá JAMÁS afecta al request. Solo requests OK
# (<400), con email de usuario (headers saneados por el proxy) y NUNCA el portal
# invitado (REGLA #8: lo del guest ni se mide acá).
@app.middleware("http")
async def _telemetria_uso(request, call_next):
    response = await call_next(request)
    try:
        if (response.status_code < 400
                and request.url.path.startswith("/api/")
                and request.headers.get("x-acaquant-portal") != "guest"):
            email = (request.headers.get("x-acaquant-user-email")
                     or request.headers.get("cf-access-authenticated-user-email"))
            if email:
                from api import telemetria

                telemetria.registrar_request(request.url.path, email)
    except Exception:  # jamás romper un request por telemetría
        pass
    return response

# Profiling opt-in (pyinstrument): solo se monta si API_PROFILING=1. Con `?profile=1`
# en cualquier request devuelve el árbol de llamadas. OFF por default → cero overhead.
if maybe_add_profiler(app):
    logger.warning("profiling ON — requests con ?profile=1 devuelven el perfil, no la data")

# Dependencies por router (RBAC por módulo):
#   verify_api_key           → bearer token (común a todos).
#   require_module(modulo)   → gate por role según core/roles.py::MODULES.
#
# Cloudflare Access ya validó que el email puede entrar al sitio;
# require_module chequea que el role del email tenga el módulo listado
# en Manager.RoleMatrix.
_PUBLIC       = [Depends(verify_api_key)]
_PORTFOLIOS   = [Depends(verify_api_key), Depends(require_module("portfolios"))]
_BACK_OFFICE  = [Depends(verify_api_key), Depends(require_module("back-office"))]
_OPERAR       = [Depends(verify_api_key), Depends(require_module("operar"))]
_OPERACIONES  = [Depends(verify_api_key), Depends(require_module("operaciones"))]
_TRADING      = [Depends(verify_api_key), Depends(require_module("trading"))]
# Módulo `ia` (QuantAI): features de IA — canary via matriz (default solo admin).
_IA           = [Depends(verify_api_key), Depends(require_module("ia"))]
# `manager.router` ya NO va con _MANAGER global: gatear todo /api/manager/*
# con el módulo `manager` excluye a `asistente_comercial` (que solo tiene
# `manager_comercial` y `manager_clientes`). El gate ahora vive POR sub-router
# en `api/routers/manager/__init__.py`. Acá dejamos solo el bearer base.
_MANAGER      = [Depends(verify_api_key), Depends(require_module("manager"))]

# Públicos (todos los roles tienen home/renta-fija/derivados/estrategia):
app.include_router(me.router)                                      # /api/me — sin gate (identidad propia)
app.include_router(ingest.router)                                  # /api/ingest — auth propia (X-Ingest-Token), no _PUBLIC
app.include_router(analitica.router,         dependencies=_PUBLIC)
app.include_router(cotizaciones.router,      dependencies=_PUBLIC)
app.include_router(calendario.router,        dependencies=_PUBLIC)
app.include_router(news.router,              dependencies=_PUBLIC)
app.include_router(market.router,            dependencies=_PUBLIC)
# Derivados Agro: GET público (todos los roles ven derivados); el PATCH
# tiene su propio gate inline trader+admin.
app.include_router(derivados_agro.router,    dependencies=_PUBLIC)
app.include_router(derivados_sinteticos.router, dependencies=_PUBLIC)
app.include_router(back_office.router,        dependencies=_BACK_OFFICE)
app.include_router(scanner.router,            dependencies=_PUBLIC)
app.include_router(research.router,           dependencies=_PUBLIC)  # Análisis Fundamental (gate renta-variable en el router)
app.include_router(research1816.router,       dependencies=_PUBLIC)  # vista RESEARCH (gate módulo `research` en el router) — docs/VISTA_RESEARCH.md
app.include_router(research_bcra.router,      dependencies=_PUBLIC)  # tab BCRA de Research (gate módulo `research` en el router) — docs/RESEARCH_BCRA.md
app.include_router(research_fred.router,      dependencies=_PUBLIC)  # tab Datos Internacionales / FRED (gate módulo `research` en el router) — docs/RESEARCH_FRED.md
app.include_router(research_docs.router,      dependencies=_PUBLIC)  # documentos manuales de REPORTES FINANCIEROS (gate módulo `research` en el router)
app.include_router(trading.router,            dependencies=_TRADING)  # vista TRADING (admin)
app.include_router(ia.router,                 dependencies=_IA)       # IA (QuantAI) — gate módulo `ia`

# ASISTENTE DE NEGOCIO (QuantAI P7): feature flag = credencial del transporte
# LLM (patrón MCP: sin credencial, el endpoint NO existe). Gate módulo
# `asistente` (admin-only por default, jamás invitado — REGLA #8).
from core import llm as _llm  # noqa: E402  (flag de montaje, no lógica)

if _llm.configurado():
    app.include_router(
        asistente.router,
        dependencies=[Depends(verify_api_key), Depends(require_module("asistente"))],
    )
else:
    logger.info("asistente de negocio deshabilitado (transporte LLM sin credencial)")

# Restringidos a roles con el módulo respectivo:
app.include_router(carteras.router,          dependencies=_PORTFOLIOS)
app.include_router(valuaciones.router,        dependencies=_PORTFOLIOS)
# titulos.router (assets + flujos) es PURO catálogo de instrumentos — sin
# info de cuentas/posiciones. Lo necesita renta-fija/page.tsx para mapear
# ticker → curva al pintar la tabla. Antes estaba bajo _PORTFOLIOS y para
# rol SALES devolvía 403 → renta-fija quedaba en "MERCADO CERRADO" porque
# allFlujos venía vacío. Pasa a _PUBLIC, igual que cotizaciones / analitica.
app.include_router(titulos.router,           dependencies=_PUBLIC)
# Acción de operar (DOLAR MEP, órdenes vivas, saldo): puede ser para sales también
app.include_router(ordenes.router,           dependencies=_OPERAR)
app.include_router(operativa.router,         dependencies=_OPERAR)
app.include_router(operar.router,            dependencies=_OPERAR)
app.include_router(risk.router,              dependencies=_OPERAR)
# Mesa / flujo / contrapartes: solo trader y admin
app.include_router(operaciones.router,       dependencies=_OPERACIONES)
app.include_router(cuentas.router,           dependencies=_OPERACIONES)
# manager.router: gate FINO por sub-router (ver api/routers/manager/__init__.py).
# Acá ponemos una base FAIL-CLOSED: exige al menos UN módulo manager. Así un
# sub-router nuevo que se agregue sin su dependency NO queda abierto a cualquier
# autenticado (antes el default era _PUBLIC = abierto). admin (manager) y
# asistente_comercial (manager_comercial/clientes) pasan la base; el gate fino
# de cada sub-router sigue restringiendo lo suyo.
_MANAGER_BASE = [
    Depends(verify_api_key),
    Depends(require_any_module(
        ("manager", "manager_comercial", "manager_clientes", "manager_clientes_bulk")
    )),
]
app.include_router(manager.router,           dependencies=_MANAGER_BASE)
# manager_resources (system monitoring): admin-only, mantiene gate `manager`.
app.include_router(manager_resources.router, dependencies=_MANAGER)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# ── MCP server (sub-app en /mcp + OAuth + discovery) ──
# Se monta si hay MCP_BEARER_TOKEN (static) o MCP_JWT_SECRET (OAuth).
# - /mcp/*                                  → MCP Streamable HTTP, gated por middleware bearer.
# - /oauth/{authorize,token,register}       → OAuth provider (solo si MCP_JWT_SECRET).
# - /.well-known/oauth-{authorization-server,protected-resource}
#                                           → discovery público (solo si MCP_JWT_SECRET).
if MCP_BEARER_TOKEN or MCP_JWT_SECRET:
    from api.mcp.auth import MCPBearerMiddleware
    from api.mcp.server import mcp as _mcp

    _mcp_app = _mcp.streamable_http_app()
    _mcp_app.add_middleware(MCPBearerMiddleware)
    app.mount("/mcp", _mcp_app)

    if MCP_JWT_SECRET:
        from api.mcp import discovery as _mcp_discovery
        from api.mcp import oauth as _mcp_oauth
        # Sin gate del API_KEY: estos endpoints definen su propia auth (CF
        # Access + DCR + PKCE). El _PUBLIC bearer del API_KEY no aplica.
        app.include_router(_mcp_discovery.router)
        app.include_router(_mcp_oauth.router)
        logger.info("MCP montado en /mcp + OAuth + discovery (JWT)")
    else:
        logger.info("MCP montado en /mcp (solo static bearer; sin OAuth)")
else:
    logger.info("MCP no configurado — /mcp deshabilitado")
