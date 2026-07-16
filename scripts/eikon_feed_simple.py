"""eikon_feed_simple.py — versión MÍNIMA, calcada del script de commodities.

Solo hace esto, nada más:
  1. Pide a la API la lista de RICs ya cargados (los sin RIC quedan afuera).
  2. Loop: ek.get_data(rics, CAMPOS) → POST a la API de last/bid/ask/high/low/
     cierre anterior/volumen/var% (ver CAMPOS abajo).

Sin symbology, sin flags, sin chunks. Config abajo (mismos valores que
mae_forex_client.py). Correr con Workspace abierto y logueado:

    python eikon_feed_simple.py
"""
import sys
import time
import warnings
from datetime import datetime

# La lib eikon (deprecada) usa una opción vieja de pandas → FutureWarning en
# cada pasada. No afecta nada; se silencia para no ensuciar la consola.
warnings.filterwarnings("ignore", category=FutureWarning)

# En consolas Windows con codepage viejo (cp1252), imprimir un emoji CRASHEA
# el script entero (UnicodeEncodeError) → nunca crashear por un print.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

def _salir(motivo: str) -> None:
    print(motivo)
    try:
        input("\n[ENTER] para cerrar...")
    except Exception:
        pass
    raise SystemExit(1)


# La ventana NUNCA se cierra sola: si falta una librería lo dice y espera ENTER.
try:
    import eikon as ek
    import pandas as pd
    import requests
except ImportError as e:
    _salir(f"Falta la librería {e.name!r} -> en la consola de este Python:  "
           f"pip install eikon requests")

# TRAMPA CLÁSICA: si guardaste ESTE archivo como 'eikon.py', el import de arriba
# se importa a sí mismo en vez de la librería y nada funciona.
if not hasattr(ek, "set_app_key"):
    _salir("El archivo NO puede llamarse eikon.py (Python lo importa a sí mismo "
           "en vez de la librería). Renombralo, por ejemplo a feed.py, y listo.")

# ════════════════════════════════════════════════════════════════════════════
# CONFIG — completá estos valores (NO commitear la copia con las keys reales)
# ════════════════════════════════════════════════════════════════════════════
EIKON_APP_KEY = "PEGA_TU_APP_KEY_ACA"

API_BASE = "https://api.acaquant.com"
INGEST_TOKEN = "PEGA_EL_X_INGEST_TOKEN_ACA"      # el mismo del mae_forex_client
CF_CLIENT_ID = ""                          # los mismos del mae_forex_client
CF_SECRET = ""

DRY_RUN = False        # True = muestra lo que mandaría, no envía nada
INTERVALO_SEG = 20
# ════════════════════════════════════════════════════════════════════════════

HEADERS = {
    "X-Ingest-Token": INGEST_TOKEN.strip(),
    "CF-Access-Client-Id": CF_CLIENT_ID.strip(),
    "CF-Access-Client-Secret": CF_SECRET.strip(),
    "Content-Type": "application/json",
}

ultimo = {}   # cache para mandar solo lo que cambió (como last_outputs en el tuyo)

# Campo Eikon → nombre en el payload. CF_* son de acciones; PRIMACT_1 es el
# last de FUTUROS (fallback, por si algún día se suscribe uno) — Eikon avisa
# "Field not found" por el que no aplique a cada instrumento: es normal.
CAMPOS = {
    "CF_LAST":   "last",        # último precio negociado
    "CF_BID":    "bid",         # compra
    "CF_ASK":    "ask",         # venta
    "CF_OPEN":   "open",        # apertura del día
    "CF_HIGH":   "high",        # máximo del día
    "CF_LOW":    "low",         # mínimo del día
    "CF_CLOSE":  "prev_close",  # cierre anterior
    "CF_VOLUME": "volumen",     # volumen del día
    "PCTCHNG":   "var_pct",     # variación % del día
    "NETCHNG_1": "var_neta",    # cambio neto en precio del día
}
FIELDS = [*CAMPOS, "PRIMACT_1"]


def actualizar_precios(universo):
    ric_a_ticker = {u["ric"]: u["ticker"] for u in universo}
    try:
        data, err = ek.get_data(list(ric_a_ticker), FIELDS)
        if err and not ultimo:   # solo la primera pasada, para no spammear
            print("(avisos de Eikon en la 1ra pasada — 'Field not found' es normal):", err)
        if data is None or data.empty:
            print("⚠️ get_data no devolvió datos.")
            return

        docs = []
        for _, row in data.iterrows():
            ric = row["Instrument"]
            if ric not in ric_a_ticker:
                continue
            doc = {"ticker": ric_a_ticker[ric], "ric": ric}
            for campo, nombre in CAMPOS.items():
                v = row.get(campo)
                doc[nombre] = float(v) if v is not None and not pd.isna(v) else None
            if doc["last"] is None:                    # fallback futuros
                v = row.get("PRIMACT_1")
                doc["last"] = float(v) if v is not None and not pd.isna(v) else None
            if doc["last"] is None:                    # sin precio no se manda
                continue
            docs.append(doc)

        cambiados = [d for d in docs if ultimo.get(d["ticker"]) != d]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if DRY_RUN:
            for d in docs[:10]:
                print("  ", d)
            print(f"[{now}] DRY_RUN — {len(docs)} precios leídos, {len(cambiados)} con cambios.")
            for d in docs:
                ultimo[d["ticker"]] = d
            return
        if not cambiados:
            print(f"[{now}] 🔄 sin cambios.")
            return
        r = requests.post(f"{API_BASE}/api/ingest/eikon/quotes",
                          json={"docs": cambiados}, headers=HEADERS, timeout=15)
        if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
            print(f"[{now}] ✅ {len(cambiados)} precios actualizados.")
            for d in docs:
                ultimo[d["ticker"]] = d
        else:
            print(f"[{now}] ❌ API {r.status_code}: {r.text[:200]}")

    except Exception as e:
        if "429" in str(e):
            print("⚠️ Límite de requests alcanzado. Esperando 60s antes de reintentar.")
            time.sleep(60)
        else:
            print("❌ Error general:", type(e).__name__, e)


def main():
    if "PEGA_TU" in EIKON_APP_KEY or "PEGA_EL" in INGEST_TOKEN:
        print("Te falta pegar EIKON_APP_KEY / INGEST_TOKEN en el bloque CONFIG del script.")
        return
    ek.set_app_key(EIKON_APP_KEY)

    r = requests.get(f"{API_BASE}/api/ingest/eikon/universo",
                     headers=HEADERS, timeout=15)
    if "json" not in r.headers.get("content-type", ""):
        print(f"La API no respondió JSON (HTTP {r.status_code}) — si es HTML de login, "
              "el service token de CF Access no fue aceptado.")
        return
    universo = [u for u in r.json()["universo"] if u.get("ric")]
    if not universo:
        print("No hay NINGÚN RIC cargado — cargalos en Manager → TÍTULOS → RENTA VARIABLE.")
        return
    print(f"suscribiendo {len(universo)} RICs (los sin RIC quedan afuera) — "
          f"loop cada {INTERVALO_SEG}s, Ctrl+C corta")

    while True:
        actualizar_precios(universo)
        time.sleep(INTERVALO_SEG)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("chau.")
    except Exception as e:
        print(f"❌ {type(e).__name__}: {e}")
    try:
        input("\n[ENTER] para cerrar…")
    except Exception:
        pass
