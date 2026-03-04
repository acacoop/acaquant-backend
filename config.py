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

AUNESA_CLIENT_ID = os.getenv("AUNESA_CLIENT_ID")
AUNESA_USERNAME = os.getenv("AUNESA_USERNAME")
AUNESA_PASSWORD = os.getenv("AUNESA_PASSWORD")


GS_SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

# --- LISTA MAESTRA DE SUBSCRIPCIÓN ---
# Todo lo que pongas acá se suscribe y se manda al Excel
TICKERS_LIST = [
    "MERV - XMEV - PESOS - 1D",
    "ORO/MAR26",
    "WTI/MAR26",
    "GGAL/ABR26",
    "GGAL/JUN26",
    "MERV - XMEV - AL30D - CI",
    "MERV - XMEV - AL30 - CI","MERV - XMEV - VSCJO - 24hs","MERV - XMEV - BVCOO - 24hs",
    "MERV - XMEV - DHSGO - 24hs","MERV - XMEV - TTCBO - 24hs","MERV - XMEV - DHSJO - 24hs",
    "MERV - XMEV - TZX26 - 24hs", "MERV - XMEV - TZXD6 - 24hs",
    "MERV - XMEV - TZXM6 - 24hs", "MERV - XMEV - TZXO6 - 24hs", "MERV - XMEV - TTS26 - 24hs",
    "MERV - XMEV - S17A6 - 24hs","MERV - XMEV - S16M6 - 24hs",
    "MERV - XMEV - AL30 - 24hs", "MERV - XMEV - GD35 - 24hs","MERV - XMEV - GD38 - 24hs",
    "MERV - XMEV - BPOC7 - 24hs","MERV - XMEV - BPOD7 - 24hs","MERV - XMEV - BA37D - 24hs",
    "MERV - XMEV - TTJ26 - 24hs","MERV - XMEV - TTD26 - 24hs","MERV - XMEV - S30A6 - 24hs",
    "MERV - XMEV - X15Y6 - 24hs",
    "MERV - XMEV - CO27D - 24hs","MERV - XMEV - CO3D7 - 24hs","MERV - XMEV - PNFCO - 24hs","MERV - XMEV - TLCDO - 24hs",
    "MERV - XMEV - TLCGO - 24hs","MERV - XMEV - YMCTO - 24hs","MERV - XMEV - TLCKO - 24hs","MERV - XMEV - TLCLO - 24hs",
    "MERV - XMEV - IRCLO - 24hs", "MERV - XMEV - YMCWO - 24hs","MERV - XMEV - GN46O - 24hs","MERV - XMEV - PN40O - 24hs",
    "MERV - XMEV - YM37O - 24hs","MERV - XMEV - RCCRO - 24hs","MERV - XMEV - PECKO - 24hs","MERV - XMEV - PECMO - 24hs",
    "MERV - XMEV - DHSHO - 24hs","MERV - XMEV - YMCWO - 24hs","MERV - XMEV - YFCOO - 24hs",
    "MERV - XMEV - TX26 - 24hs","MERV - XMEV - GD41 - 24hs","MERV - XMEV - TZXD7 - 24hs", "MERV - XMEV - TTM26 - 24hs",
    "MERV - XMEV - BPOB8 - 24hs","MERV - XMEV - D30A6 - 24hs","MERV - XMEV - S30O6 - 24hs","MERV - XMEV - T30A7 - 24hs",
    "MERV - XMEV - X29Y6 - 24hs", "MERV - XMEV - AN29 - 24hs", "MERV - XMEV - S30N6 - 24hs","MERV - XMEV - CP39O - 24hs",
    "MERV - XMEV - T31Y7 - 24hs", "MERV - XMEV - X30N6 - 24hs", "MERV - XMEV - AO27 - 24hs", "MERV - XMEV - SFD34 - 24hs",
    "MERV - XMEV - BACGO - 24hs", "MERV - XMEV - OLC2O - 24hs", "MERV - XMEV - BUM26 - 24hs", "MERV - XMEV - AFCHO - 24hs",
    "MERV - XMEV - AER9O - 24hs", "MERV - XMEV - LUC4O - 24hs", "MERV - XMEV - PQCKO - 24hs","MERV - XMEV - LECHO - 24hs"]

