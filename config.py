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

# --- LLM / IA GENERATIVA ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# 'claude' (default) usa router haiku/sonnet; 'gemini' usa Flash legacy.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "claude").lower()

# --- DATA DE MERCADO EXTERNA ---
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

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
