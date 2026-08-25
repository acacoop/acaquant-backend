import os

from dotenv import load_dotenv

# Cargamos las variables del archivo .env
load_dotenv()

class Config:
    USER = os.getenv("ROFEX_USER")
    PASSWORD = os.getenv("ROFEX_PASSWORD")
    ACCOUNT = os.getenv("ROFEX_ACCOUNT")
    URL = os.getenv("ROFEX_API_URL")
    WS = os.getenv("ROFEX_WS_URL")

# --- AUNESA CONFIG ---
AUNESA_CLIENT_ID = os.getenv("AUNESA_CLIENT_ID")
AUNESA_USERNAME = os.getenv("AUNESA_USERNAME")
AUNESA_PASSWORD = os.getenv("AUNESA_PASSWORD")

# --- INTERBANKING CONFIG ---
# Credenciales de la aplicación dada de alta en el portal de APIs de Interbanking.
# CUSTOMER_ID es el "código de abonado" de la empresa (formato ^[A-Z][0-9]{5}[A-Z]$),
# que sale de Interbanking → Administración → ABM → Datos de Empresa. Es un dato
# DISTINTO del client_id: identifica a la empresa, no a la aplicación.
INTERBANKING_CLIENT_ID = os.getenv("INTERBANKING_CLIENT_ID")
INTERBANKING_CLIENT_SECRET = os.getenv("INTERBANKING_CLIENT_SECRET")
INTERBANKING_CUSTOMER_ID = os.getenv("INTERBANKING_CUSTOMER_ID")

# Los tres siguientes son overrides de diagnóstico: existen para que, cuando
# `scripts/diag_interbanking_auth` encuentre la combinación que funciona, se
# active desde el .env sin tocar código ni redeployar. El default es lo que
# declara el propio servidor en su documento de descubrimiento OIDC — que NO
# coincide con el tokenUrl de los YAML del proveedor (ver docs/INTERBANKING.md).
INTERBANKING_TOKEN_URL = os.getenv(
    "INTERBANKING_TOKEN_URL",
    "https://auth.interbanking.com.ar/cas/oidc/oidcAccessToken",
)
INTERBANKING_AUTH_STYLE = os.getenv("INTERBANKING_AUTH_STYLE", "basic")  # basic | post
INTERBANKING_SCOPE = os.getenv("INTERBANKING_SCOPE", "info-financiera")

# --- POSTRADE (A3 Mercados / Argentina Clearing — anywhereportfolio) ---
# Usuario y contraseña que asigna ACyRSA. NO hay client_id ni secret: la API
# entrega un token de 24hs a cambio de esas dos cosas (ver docs/POSTRADE.md).
POSTRADE_USUARIO = os.getenv("POSTRADE_USUARIO", "")
POSTRADE_PASSWORD = os.getenv("POSTRADE_PASSWORD", "")

# Base URL. Default PRODUCCIÓN; el entorno de pruebas es
# https://demoapi.anywhereportfolio.com.ar (el PDF documenta los ejemplos con
# ese host, pero el que nos habilitaron es prod).
POSTRADE_BASE_URL = os.getenv(
    "POSTRADE_BASE_URL", "https://api.anywhereportfolio.com.ar"
).rstrip("/")

# Cómo se mandan las credenciales al pedir el token: `body` (JSON) o `query`
# (querystring). La API acepta las dos; el default es body para no dejar la
# contraseña escrita en la URL (logs de proxy, access logs, historial).
POSTRADE_AUTH_STYLE = os.getenv("POSTRADE_AUTH_STYLE", "body")  # body | query

# Prefijo del header Authorization en las llamadas ya autenticadas.
#
# **MEDIDO, y el manual escrito NO lo dice**: el texto solo escribe «incluir el
# header Authorization el token obtenido» y el formato exacto vive en una
# CAPTURA DE PANTALLA (pág. 6), que dice `Authorization: Token <token>`.
# Sin el prefijo `Token `, la API responde HTTP 200 con
# `{"Status":"Unauthorized","Code":"401","ErrorDescription":"Invalid
# Authorization header."}` — nótese "header": el mensaje habla del FORMATO del
# header, no de las credenciales, y es lo que distingue este caso del de una
# contraseña mal puesta (que dice `"Invalid Authorization"`, sin "header").
# Confundir los dos manda a reclamarle permisos al proveedor por un error nuestro.
POSTRADE_TOKEN_PREFIJO = os.getenv("POSTRADE_TOKEN_PREFIJO", "Token ")

# ⚠️ ESCRITURA CONTRA POSTRADE — default DENY.
# A diferencia de Interbanking (100% lectura, no podía mover plata ni por error),
# Postrade tiene métodos con efecto REAL: NewOrderSingle suscribe y rescata FCI,
# CancelOrder cancela, AccountStatus inactiva una cuenta y ChangePassword nos deja
# afuera de nuestra propia integración. Prender esto es una decisión de OPERACIÓN,
# no de desarrollo; y aun prendido, cada llamada tiene que pedirlo explícito
# (ver core/postrade.escribir). Ver docs/POSTRADE.md.
POSTRADE_ESCRITURA = os.getenv("POSTRADE_ESCRITURA", "").strip().lower() in ("1", "true", "yes")

# --- API KEY ---
API_KEY = os.getenv("API_KEY", "")

# Token dedicado del endpoint de ingesta del dólar oficial (POST /api/ingest/
# dolar-oficial). La PC de oficina (mae_forex) lo manda en el header X-Ingest-Token.
# Acotado a propósito: si se filtra, solo permite escribir DolarOficialLive — NO da
# acceso a Mongo. Sin él seteado, el endpoint responde 503 (ingesta deshabilitada).
DOLAR_INGEST_TOKEN = os.getenv("DOLAR_INGEST_TOKEN", "")

# Entorno de ejecución. `prod` activa el fail-closed de auth (EXT-AUTH1):
# si ENV=prod y falta API_KEY, la API NO arranca (mejor caída ruidosa que
# quedar abierta en silencio). Default `dev` → comportamiento permisivo
# (no rompe local). Setear ENV=prod en el systemd unit del Droplet.
ENV = os.getenv("ENV", "dev").strip().lower()

# --- Profiling de requests (pyinstrument) ---
# OFF por default. Si se setea a "1"/"true", se monta el middleware que, ante
# `?profile=1` en cualquier request, devuelve el árbol de llamadas (HTML) o el
# JSON speedscope (`?profile=speedscope`) en vez de la respuesta normal.
# Pensado para prender temporalmente y diagnosticar un endpoint lento; dejarlo
# OFF en prod salvo durante una sesión de medición (el output expone la
# estructura interna). Cero overhead cuando está OFF: el middleware ni se monta.
API_PROFILING = os.getenv("API_PROFILING", "").strip().lower() in ("1", "true", "yes")

# --- MCP server (Model Context Protocol) ---
# Auth en /mcp tiene 2 caminos:
#   1) OAuth (para Claude Desktop / claude.ai / Claude Code via Custom
#      Connector dialog): el server emite JWTs firmados con MCP_JWT_SECRET
#      después que el user pase por CF Access en /oauth/authorize.
#   2) Static bearer (para curl, scripts, dev): MCP_BEARER_TOKEN.
#      Sigue funcionando como fallback.
# Si MCP_BEARER_TOKEN está vacío Y MCP_JWT_SECRET está vacío, /mcp queda
# DESHABILITADO (no se monta).
MCP_BEARER_TOKEN = os.getenv("MCP_BEARER_TOKEN", "")
MCP_JWT_SECRET   = os.getenv("MCP_JWT_SECRET", "")
# Issuer que va en los JWTs OAuth-issued. Default: la URL pública del API.
MCP_OAUTH_ISSUER = os.getenv("MCP_OAUTH_ISSUER", "https://api.acaquant.com")
# Hosts permitidos en redirect_uris del DCR (anti open-redirect / robo de token).
# Suffix match sobre el host. Default: dominios de Claude. Coma-separado.
_mcp_redirect_env = os.getenv("MCP_ALLOWED_REDIRECT_HOSTS", "claude.ai,claude.com")
MCP_ALLOWED_REDIRECT_HOSTS: set[str] = {
    h.strip().lower() for h in _mcp_redirect_env.split(",") if h.strip()
}

# --- DATA DE MERCADO EXTERNA ---
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

# --- TICKERS EXTRA (precio crudo, sin enrichment) ---
# motor_rofex se suscribe a estos para tener precio live en TimeSales,
# pero motor_curvas los IGNORA (no calcula TEA/duration porque no están
# en Trading.Curvas). Se usan para análisis derivados que solo necesitan
# precio: ej. canje AL30C/AL30D, brecha CCL/MEP, etc.
TICKERS_EXTRA_PRECIOS: list[str] = [
    "MERV - XMEV - AL30C - 24hs",  # canje AL30C/AL30D
    # Operativa MEP (api/services/operativa_mep.py): la versión pesos (AL30
    # sin D) no está en Curvas — bonares se indexan solo con sufijo D — y
    # las ruedas CI tampoco. Las suscribimos acá para que motor_rofex
    # alimente Trading.TimeSales y la UI tenga MEP live.
    "MERV - XMEV - AL30 - CI",
    "MERV - XMEV - AL30D - CI",
    "MERV - XMEV - AL30 - 24hs",
    "MERV - XMEV - AL30D - 24hs",
]

# --- ESTRATEGIA QUANT (vista TRADING → tab ESTRATEGIA) ---
# Doc vivo: docs/ESTRATEGIA_QUANT.md. Universo FIJO que vigila el motor
# engines/estrategia.py (fijo a propósito: el track-record necesita un universo
# estable — si emite solo para lo que el trader mira, el dataset queda sesgado).
ESTRATEGIA_TICKERS: list[str] = [
    "NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "MELI",
    "RKLB", "ASTS", "SNDK", "PLTR", "AMD", "KO", "VIST", "GGAL",
]
# Índices de referencia posibles (deben ser CEDEARs activos con snapshot live).
ESTRATEGIA_INDICES: list[str] = ["QQQ", "SPY"]
# Emisión al ledger: |score| mínimo + cooldown por ticker (no spamear señales).
ESTRATEGIA_SCORE_UMBRAL = 40.0
ESTRATEGIA_COOLDOWN_MIN = 15
# Horizontes de resolución (min) y objetivo/stop del "toco_objetivo" (%).
ESTRATEGIA_HORIZONTES: list[int] = [15, 30, 60]
ESTRATEGIA_OBJETIVO_PCT = 0.5
ESTRATEGIA_STOP_PCT = 0.5

# --- ESTRATEGIA: contexto determinista (ATR + Efficiency Ratio) ---
# Universo FOCO del panel CONTEXTO de la vista ESTRATEGIA: ATR-20 (rango típico
# diario, mercado.cedears_ohlc_daily) + Efficiency Ratio intradía (choppy, sobre
# mercado.cedears_bars_1m / tape). Son ticker_corto. Se muestran estos aunque el
# ATR/ER se calcule para TODOS los CEDEARs (ver docs/ESTRATEGIA_QUANT.md).
ESTRATEGIA_CONTEXTO_TICKERS: list[str] = ["QQQ", "SPY", "SNDK", "NVDA", "RKLB"]
# Umbral de ER por debajo del cual la rueda se considera choppy (no operar niveles).
ESTRATEGIA_ER_CHOPPY = 0.30


# --- Canje (par CCL/MEP por bono) ---
# Tickers C (CCL) y D (MEP) de cada par para la vista /analitica/canje. Vive en
# config (no en el service) para que jobs/cierre_canje.py lo comparta sin que
# jobs/ importe api/services (regla de capas). El cron materializa el cierre
# diario de estos tickers en Trading.CanjeCierre → serie_canje lee ~365 docs en
# vez de agregar ~540k ticks de TimeSales.
PARES_CANJE: dict[str, dict[str, str]] = {
    "AL30": {
        "c": "MERV - XMEV - AL30C - 24hs",  # CCL
        "d": "MERV - XMEV - AL30D - 24hs",  # MEP
    },
    "GD30": {
        "c": "MERV - XMEV - GD30C - 24hs",
        "d": "MERV - XMEV - GD30D - 24hs",
    },
}

# --- ACCESO MANAGER ---
# Emails con acceso a la vista Manager. Separados por coma en MANAGER_EMAILS o hardcodeados aquí.
_manager_env = os.getenv("MANAGER_EMAILS", "")
MANAGER_EMAILS: set[str] = {
    e.strip().lower()
    for e in _manager_env.split(",")
    if e.strip()
}

# --- CLOUDFLARE ACCESS (validación JWT) ---
# Team domain (sin https://, sin /cdn-cgi/access/certs). Ej: "acaquant".
# Si está vacío, el JWT no se valida y se cae al header spoofable (modo dev).
CF_ACCESS_TEAM = os.getenv("CF_ACCESS_TEAM", "").strip()
# Audience tag (AUD) del application en Cloudflare Zero Trust. Sin esto no
# se valida el JWT. Se obtiene en Zero Trust → Access → Applications →
# Application → Overview → "Application Audience (AUD) Tag".
CF_ACCESS_AUD = os.getenv("CF_ACCESS_AUD", "").strip()

# Service tokens de Cloudflare Access que se consideran "admin" automáticamente.
# Estos son las identidades de MÁQUINA (ej. el frontend Vercel llamando al API
# backend) que ya pasaron por su propio gate antes de llegar acá.
# Formato: common_names separados por coma, ej: "acaquant-web-prod,acaquant-web-preview".
_trusted_cf_env = os.getenv("CF_TRUSTED_SERVICE_TOKENS", "")
CF_TRUSTED_SERVICE_TOKENS: set[str] = {
    t.strip().lower()
    for t in _trusted_cf_env.split(",")
    if t.strip()
}

# --- GUARDRAILS DE DATOS (docs/OBSERVABILIDAD_ROBUSTEZ.md, commit 2) ---
# Umbrales de los invariantes de sanidad post-cierre (jobs/guardrails.py).
# None = SIN CALIBRAR: el check corre igual (muestra el valor real medido)
# pero JAMÁS marca violación. Calibrar corriendo varios días
# `python -m jobs.guardrails` y fijando acá valores sensatos con esos
# números en la mano (REGLA #2: nada de umbrales a ojo).
GUARDRAILS_UMBRALES: dict[str, float | None] = {
    "aum_delta_pct": None,        # |Δ%| del AuM total día-contra-día
    "salto_precio_pct": None,     # |Δ%| del precio de cierre por bono vs cierre previo
    "cobertura_curva_pct": None,  # % mínimo de bonos del master con cierre en el día
    "especies_cruzadas_max": None,  # bonos cuyo instrumento no es de su moneda
    # Corporativos cuyo emisor no tiene industria. Arranca en None (= no marca
    # violación) igual que el resto: el catálogo recién se siembra y hoy son 51.
    # Se fija cuando la mesa termine de cargar — la idea es que el número BAJE y
    # el umbral lo sostenga, no al revés.
    "emisores_sin_industria_max": None,
}

# --- CUPO TRANSACCIONAL ---
# `clientes.comitentes.cupo_usado_ars` es una FOTO cargada a mano el 2026-06-01
# (medido: `cupo_cargado_en` quedó NULL en las 1.567 cuentas, así que la fecha no
# está en la base). Desde ese día el cupo usado REAL se mueve con la plata que
# entra y sale de cada cliente — el mismo flujo que ya muestra la vista CASHFLOW.
# El valor vivo se calcula al LEER (`cashflow_sql.neto_por_cuenta`), no se
# persiste: así no hay job que pueda doble-contar ni backfill que revertir.
# Si algún día se recarga el cupo, hay que mover esta fecha al día de la carga.
CUPO_BASE_FECHA = "2026-06-01"

# --- AP5 · REQUERIMIENTO DE MÁRGENES ---
# Las cuentas cuyo margen SUMA en la card de la cabecera. El job guarda TODAS
# las que devuelve la cámara en `ap5.margenes` (sirven para otra cosa y son
# gratis: ya vinieron en la misma respuesta); esta lista es solo el recorte que
# se MUESTRA.
#
# ⚠️ **Es un PAR (cuenta de neteo, cuenta de compensación), no una cuenta.**
# REGLA #9(A): las dos se emparejan distinto — `149667` cuelga de la compensación
# `1172` y `218115` cuelga de sí misma. Con la cuenta sola, el día que un mismo
# comitente aparezca bajo dos compensaciones la card sumaría de más sin fallar.
#
# Cambiar esta lista cambia el número de la pantalla y nada más: la tabla sigue
# guardando todo, así que agregar o sacar una cuenta no pierde historia ni
# obliga a un backfill.
AP5_CUENTAS_REQUERIMIENTO: tuple[tuple[str, str], ...] = (
    ("149667", "1172"),
    ("218115", "218115"),
)

# --- AP5 · QUÉ CONCEPTOS SUMA CADA CARD ---
# `Reference` es el NOMBRE del concepto, no un id. Medido el 2026-08-25 sobre
# 31 referencias: `Márgenes` (×28), `Inicial A3`, `Inicial FGIMC`, `Cauciones $`.
#
# **El activo integrado lo verificó el user contra el número real de la mesa:
# `Márgenes + Inicial A3`.** No se dedujo — se comparó.
#
# ⚠️ **El requerimiento NO está verificado todavía.** Arranca en `Márgenes` sola
# porque es lo literal, pero eso es una HIPÓTESIS: si el número de la card no
# coincide con el del mail, el que hay que cambiar es este renglón y nada más.
# Está escrito acá justamente para que corregirlo no sea tocar código.
#
# Los conceptos que NO están en ninguna lista igual se guardan en `ap5.margenes`
# — la tabla es el registro completo y estas listas son sólo el recorte que se
# muestra.
AP5_CONCEPTOS_REQUERIMIENTO: tuple[str, ...] = ("Márgenes",)
AP5_CONCEPTOS_ACTIVO_INTEGRADO: tuple[str, ...] = ("Márgenes", "Inicial A3")

# La cámara manda estos importes en NEGATIVO y la mesa los lee en positivo. Se
# guardan con el signo original (dar vuelta el dato en la capa que lo trae es
# cómo se pierde de vista qué mandó de verdad el proveedor) y se invierten UNA
# vez, acá, al mostrarlos.
AP5_MARGENES_INVERTIR_SIGNO = True
