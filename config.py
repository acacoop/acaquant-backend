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

# --- API KEY ---
API_KEY = os.getenv("API_KEY", "")

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

# --- Alertas (Telegram) ---
# Bot creado con @BotFather. Si falta cualquiera de los dos, las alertas
# quedan deshabilitadas (no-op silencioso, ver core/notify.py). El token es
# un SECRETO → va en env (.env / systemd unit), nunca en el repo.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

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

# --- LLM / IA GENERATIVA ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# 'claude' (default) usa router haiku/sonnet; 'gemini' usa Flash legacy.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "claude").lower()

# --- DATA DE MERCADO EXTERNA ---
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

# --- BYMA Primarias Placements (licitaciones / colocaciones primarias) ---
# OAuth2 client_credentials flow. Credenciales desde el portal BYMA Developer.
# Scope requerido para los endpoints actuales: bymaPrimariasPlacements.read
BYMA_CLIENT_ID     = os.getenv("BYMA_CLIENT_ID", "")
BYMA_CLIENT_SECRET = os.getenv("BYMA_CLIENT_SECRET", "")
BYMA_TOKEN_URL     = os.getenv("BYMA_TOKEN_URL", "https://apigw.byma.com.ar/oauth/token/")
BYMA_BASE_URL      = os.getenv(
    "BYMA_BASE_URL",
    "https://apigw.byma.com.ar/byma-primarias-placements/v1",
)

# --- MAE MarketData (repos, cauciones, títulos wholesale) ---
# Auth simple por x-api-key. 2 ambientes: prod y uat. Default prod.
MAE_API_KEY = os.getenv("MAE_API_KEY", "")
MAE_ENV     = os.getenv("MAE_ENV", "prod").lower()  # 'prod' | 'uat'

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

# --- EXPORT API PROVEEDOR (ACAPortfolio.Cartera) ---
# id_cuenta de las cuentas cuyo AuM se expone a la API externa del
# proveedor (jobs/partner_export.py). SOLO estas cuentas salen — el job
# aborta si la lista está vacía (fail-safe: nunca exporta todo por error).
PARTNER_EXPORT_CUENTAS: list[str] = [
    "101",
    "175",
    "463",
]

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
