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

# --- ACCESO MANAGER ---
# Emails con acceso a la vista Manager. Separados por coma en MANAGER_EMAILS o hardcodeados aquí.
_manager_env = os.getenv("MANAGER_EMAILS", "")
MANAGER_EMAILS: set[str] = {
    e.strip().lower()
    for e in _manager_env.split(",")
    if e.strip()
}
