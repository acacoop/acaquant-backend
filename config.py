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

# --- GOOGLE SHEETS CONFIG ---
GS_CREDS_FILE = 'ons-fx.json'
SPREADSHEET_NAME = "Inversiones"
SHEET_MARKET = "MARKET DATA"

GS_SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

# --- LISTA MAESTRA DE SUBSCRIPCIÓN ---
# Todo lo que pongas acá se suscribe y se manda al Excel
TICKERS_LIST = ["MERV - XMEV - PESOS - 1D",
                "ORO/MAR26",
                "WTI/MAR26",
                "GGAL/ABR26",
                "GGAL/FEB26",
                "MERV - XMEV - AL30D - CI",
                "MERV - XMEV - AL30 - CI"]