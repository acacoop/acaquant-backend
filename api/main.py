"""TradingAV API — FastAPI entrypoint.

Uso:
    uvicorn api.main:app --reload --port 8000

Auth:
    Todos los endpoints (excepto /api/health) requieren header
    Authorization: Bearer <API_KEY>.
    Si API_KEY no está definida en .env, auth está desactivada (modo dev).
"""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import ORJSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from api.auth import (
    is_guest_portal,
    path_permitido_invitado,
    require_any_module,
    require_module,
)
from api.deps import verify_api_key
from api.profiling import maybe_add_profiler
from api.ratelimit import limiter
from api.routers import (
    aca,
    agente,
    analitica,
    ap5,
    back_office,
    carteras,
    cotizaciones,
    cuentas,
    derivados_agro,
    derivados_sinteticos,
    estrategia,
    ia,
    ingest,
    interbanking,
    manager,
    market,
    me,
    mesa_dinero,
    news,
    operaciones,
    operar,
    operativa,
    ordenes,
    research1816,
    research_bcra,
    research_docs,
    research_fred,
    risk,
    scanner,
    senebis,
    titulos,
    trading,
    valuaciones,
)
from config import (
    API_KEY,
    CF_ACCESS_AUD,
    CF_ACCESS_TEAM,
    ENV,
    EXT_JWT_SECRET,
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
    """Arranque/parada de la API: valida la postura de auth y, si MCP está
    configurado (token estático o JWT secret), levanta su session manager.

    Ya no hay tasks de fondo acá: el sampler de recursos del Droplet se eliminó
    junto con la tab RECURSOS (la salud del sistema vive en OBSERVABILIDAD → SALUD).
    """
    _validar_postura_auth()

    if MCP_BEARER_TOKEN or MCP_JWT_SECRET:
        from api.mcp.server import mcp as mcp_server
        async with mcp_server.session_manager.run():
            yield
    else:
        yield


# Swagger/ReDoc/openapi.json: ABIERTOS en dev, CERRADOS en prod. No llevan
# `verify_api_key` (FastAPI los monta antes que cualquier dependency), así que
# en prod publicaban el mapa completo de los ~360 endpoints con sus parámetros
# a quien alcance el origen. Los tapa Cloudflare Access, pero eso es una sola
# capa y el inventario de la superficie es justo lo que un atacante quiere
# primero. En dev siguen disponibles porque son la forma de explorar la API.
_DOCS_ABIERTOS = ENV != "prod"

app = FastAPI(
    title="TradingAV API",
    version="0.1.0",
    lifespan=lifespan,
    # orjson es 2-3x más rápido que el stdlib json en payloads grandes
    # (snapshots de portfolios, listas de boletos, series de TimeSales)
    # y aloca proporcionalmente menos RAM durante la serialización.
    default_response_class=ORJSONResponse,
    docs_url="/docs" if _DOCS_ABIERTOS else None,
    redoc_url="/redoc" if _DOCS_ABIERTOS else None,
    openapi_url="/openapi.json" if _DOCS_ABIERTOS else None,
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


# Security headers. La API sirve JSON (no hay superficie de XSS propia), así que
# el valor es acotado — pero son gratis y cubren el caso de que algo renderice
# una respuesta en un browser. `nosniff` evita que el browser adivine el tipo;
# `DENY` impide que cualquier página embeba la API en un iframe; HSTS fuerza
# HTTPS en el borde. No se agrega CSP: no servimos HTML propio.
@app.middleware("http")
async def _security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Strict-Transport-Security", "max-age=31536000; includeSubDomains",
    )
    return response


# REGLA #8 — corte duro del portal INVITADO (www.acaquant.com). El check dentro
# de `require_module` solo alcanza a los routers gateados; acá se cierra todo lo
# demás de una sola vez y en un solo lugar auditable. Va como middleware (no
# dependency) para que cubra también a los routers que se agreguen mañana.
@app.middleware("http")
async def _guard_portal_invitado(request, call_next):
    if is_guest_portal(request) and not path_permitido_invitado(request.url.path):
        logger.warning("portal invitado bloqueado: %s", request.url.path)
        return JSONResponse(
            {"detail": "no disponible en el portal de invitados"}, status_code=403,
        )
    return await call_next(request)

# GZip: /historico/trades puede devolver hasta 10K trades JSON (~1-3 MB).
# Compresión ~80% en JSON. minimum_size=1024 evita overhead en responses chicas.
app.add_middleware(GZipMiddleware, minimum_size=1024)


# Telemetría de LATENCIA por endpoint (manager.latencia_endpoints). Reemplaza a
# la telemetría de USO (decomisada 2026-08-04 — nunca se usó). Solo memoria en
# el hot path (el flush a SQL corre en thread cada ~60s); un fallo acá JAMÁS
# afecta al request. Sin identidad — mide endpoints, no usuarios.
@app.middleware("http")
async def _telemetria_latencia(request, call_next):
    import time as _time
    t0 = _time.perf_counter()
    response = await call_next(request)
    try:
        if request.url.path.startswith("/api/"):
            from api import telemetria

            telemetria.registrar_request(
                request.url.path,
                (_time.perf_counter() - t0) * 1000,
                response.status_code,
            )
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
# MESA DE DINERO: NO va con _OPERACIONES. El acceso a esa vista se decide por
# PERSONA (allowlist en Manager → MESA), no por puesto — ver el docstring de
# api/routers/mesa_dinero.py. Es el mismo criterio que `require_control_comercial`.
_MESA_DINERO  = [Depends(verify_api_key), Depends(mesa_dinero.require_lectura_mesa)]
# ACA (resumen ejecutivo de la cartera propia): el gate es el módulo `aca` UNIDO a
# la allowlist de escritura de Mesa de Dinero — escribir implica ver, y la mesa
# maneja la cuenta sin necesariamente tener el rol `empleado_aca`. Esa unión vive
# en el service (aca.puede_ver), no acá, para que /api/me y el router la resuelvan
# con la misma función. Ver api/routers/aca.py::require_lectura_aca.
_ACA          = [Depends(verify_api_key), Depends(aca.require_lectura_aca)]
# Módulo `ia` (QuantAI): features de IA — canary via matriz (default solo admin).
_IA           = [Depends(verify_api_key), Depends(require_module("ia"))]
# `manager.router` ya NO va con un gate `manager` global: gatear todo
# /api/manager/* con ese módulo excluye a `asistente_comercial` (que solo tiene
# `manager_comercial` y `manager_clientes`). El gate vive POR sub-router en
# `api/routers/manager/__init__.py`; acá abajo va solo la base fail-closed.

# Públicos (todos los roles tienen home/renta-fija/derivados/estrategia):
app.include_router(me.router)                                      # /api/me — sin gate (identidad propia)
# /api/avisos — sin gate de módulo A PROPÓSITO (2026-08-19). El AV AGENT es
# admin-only, pero lo que el agente MANDA le tiene que llegar a cualquiera: un
# trader no tiene el módulo `ia`, así que bajo /api/ia el aviso quedaba guardado
# para nadie. Devuelve SOLO los del email que pregunta — no hay parámetro para
# pedir los de otro. Ver api/routers/avisos.py.
app.include_router(ingest.router)                                  # /api/ingest — auth propia (X-Ingest-Token), no _PUBLIC
app.include_router(analitica.router,         dependencies=_PUBLIC)
app.include_router(cotizaciones.router,      dependencies=_PUBLIC)
app.include_router(news.router,              dependencies=_PUBLIC)
app.include_router(market.router,            dependencies=_PUBLIC)
# Derivados Agro: GET público (todos los roles ven derivados); el PATCH
# tiene su propio gate inline trader+admin.
app.include_router(derivados_agro.router,    dependencies=_PUBLIC)
app.include_router(derivados_sinteticos.router, dependencies=_PUBLIC)
app.include_router(back_office.router,        dependencies=_BACK_OFFICE)
app.include_router(interbanking.router,       dependencies=_BACK_OFFICE)  # INTERBANKING: extractos de los bancos (solo lectura)
app.include_router(senebis.router,            dependencies=_BACK_OFFICE)  # SENEBIS: órdenes trader → back office
app.include_router(scanner.router,            dependencies=_PUBLIC)
app.include_router(research1816.router,       dependencies=_PUBLIC)  # vista RESEARCH (gate módulo `research` en el router) — docs/VISTA_RESEARCH.md
app.include_router(research_bcra.router,      dependencies=_PUBLIC)  # tab BCRA de Research (gate módulo `research` en el router) — docs/RESEARCH_BCRA.md
app.include_router(research_fred.router,      dependencies=_PUBLIC)  # tab Datos Internacionales / FRED (gate módulo `research` en el router) — docs/RESEARCH_FRED.md
app.include_router(research_docs.router,      dependencies=_PUBLIC)  # documentos manuales de REPORTES FINANCIEROS (gate módulo `research` en el router)
app.include_router(trading.router,            dependencies=_TRADING)  # vista TRADING (admin)
app.include_router(estrategia.router,          dependencies=_TRADING)  # TRADING → tab ESTRATEGIA (docs/ESTRATEGIA_QUANT.md)
app.include_router(ia.router,                 dependencies=_IA)       # IA — observabilidad + briefing (gate módulo `ia`)
# EL AV AGENT (docs/AGENT_2.0.md). Mismo gate de módulo que /api/ia, y
# ADEMÁS `require_admin` en cada ruta del router: el agente habla del
# estado interno del sistema y `ia` lo tiene la mesa entera.
app.include_router(agente.router,             dependencies=_IA)

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
# AP5 — POSICIONES Y DIFERENCIAS: la posición de futuros que informa la CÁMARA
# (A3/ACyRSA). Router aparte de `operaciones` porque la fuente es otra: aquel lee
# nuestro registro de boletos y este lo que la cámara liquidó. Ver docs/POSTRADE.md.
app.include_router(ap5.router,               dependencies=_OPERACIONES)
# Mesa de Dinero (vista NEGOCIO): acceso por allowlist per-usuario (NO por el
# módulo `operaciones`); la escritura suma su propia allowlist adentro del router.
app.include_router(mesa_dinero.router,       dependencies=_MESA_DINERO)
# ACA (resumen ejecutivo): lectura módulo `aca` ∪ escritores de la mesa; la
# escritura suma su gate propio adentro del router.
app.include_router(aca.router,               dependencies=_ACA)
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


# ── API EXTERNA para accionistas (sub-app en /ext) ──
# docs/API_EXTERNA.md. Se monta SÓLO si hay EXT_JWT_SECRET: sin esa variable la
# superficie no existe. Borrarla del .env y reiniciar apaga la API externa entera
# sin tocar una línea de código — y volver a ponerla la enciende igual de rápido.
#
# ⚠️ Va MONTADA (`mount`) y no incluida (`include_router`) a propósito. Un router
# de la mesa que se agregue mañana es físicamente inalcanzable desde /ext: el
# default-deny es topología, no una allowlist que alguien tenga que mantener
# (comparar con GUEST_PATH_PREFIXES, REGLA #8).
#
# La auth NO es la de /api: no lleva verify_api_key ni CF-JWT de usuario. Es API
# key propia → token corto (api/ext/auth.py), con Cloudflare Access en modo
# Service Auth por delante.
if EXT_JWT_SECRET:
    from api.ext.app import ext_app as _ext_app

    app.mount("/ext", _ext_app)
    logger.info("API externa montada en /ext (accionistas)")
else:
    logger.info("API externa no configurada — /ext deshabilitado (falta EXT_JWT_SECRET)")
