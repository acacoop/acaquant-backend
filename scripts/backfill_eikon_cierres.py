"""backfill_eikon_cierres.py — BACKFILL one-shot de cierres offshore (Paso 1).

Baja de Eikon la serie de cierres diarios (CLOSE) de cada bono OFFSHORE de la
watchlist y la postea por HTTP a `POST /api/ingest/eikon/cierres`, que la
persiste en `mercado.eikon_cierres` (grupo='bonos_off'). Con eso las columnas
7D/MTD/YTD de la sección ARGENTINA dejan de mostrar "—".

La PC NO toca la base: manda todo por HTTP (mismo riel que eikon_feed_simple).
El forward diario lo sigue haciendo el cron jobs/eikon_cierres — esto solo
rellena la historia hacia atrás UNA vez. Idempotente: re-correrlo pisa los
mismos días. Descartable — se borra apenas confirmemos que quedó (REGLA #5).

Correr en la NOTEBOOK de oficina (Workspace abierto y logueado):

    python scripts/backfill_eikon_cierres.py        # DRY_RUN: no manda nada
    # revisás lo que imprime, ponés DRY_RUN = False y lo corrés de nuevo
"""
import sys
import warnings
from datetime import date, timedelta

warnings.filterwarnings("ignore", category=FutureWarning)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

# ════════════════════════════════════════════════════════════════════════════
# CONFIG — mismos valores que eikon_feed_simple.py (NO commitear con las keys)
# ════════════════════════════════════════════════════════════════════════════
EIKON_APP_KEY = "PEGA_TU_APP_KEY_ACA"

API_BASE = "https://api.acaquant.com"
INGEST_TOKEN = "PEGA_EL_X_INGEST_TOKEN_ACA"      # el mismo del feed
CF_CLIENT_ID = ""                                # los mismos del feed
CF_SECRET = ""

DRY_RUN = True         # True = muestra lo que mandaría, NO envía nada
DIAS_ATRAS = 400       # ventana hacia atrás (cubre YTD con margen)
# ════════════════════════════════════════════════════════════════════════════

HEADERS = {
    "X-Ingest-Token": INGEST_TOKEN.strip(),
    "CF-Access-Client-Id": CF_CLIENT_ID.strip(),
    "CF-Access-Client-Secret": CF_SECRET.strip(),
    "Content-Type": "application/json",
}


def _salir(motivo: str) -> None:
    print(motivo)
    raise SystemExit(1)


def main() -> None:
    try:
        import eikon as ek
        import pandas as pd
        import requests
    except ImportError as e:
        _salir(f"Falta la librería {e.name!r} -> pip install eikon pandas requests")

    if not hasattr(ek, "set_app_key"):
        _salir("El archivo no puede llamarse eikon.py (Python se importa a sí mismo).")
    if "PEGA_TU_APP_KEY" in EIKON_APP_KEY:
        _salir("Completá EIKON_APP_KEY arriba con tu app key (la del feed).")
    if "PEGA_EL_X_INGEST_TOKEN" in INGEST_TOKEN:
        _salir("Completá INGEST_TOKEN arriba (el mismo del feed eikon_feed_simple).")

    ek.set_app_key(EIKON_APP_KEY.strip())

    # Universo desde la API (misma fuente que el feed → no duplicamos el mapeo).
    r = requests.get(f"{API_BASE}/api/ingest/eikon/bonos/universo", headers=HEADERS, timeout=30)
    r.raise_for_status()
    universo = r.json().get("universo") or []
    if not universo:
        _salir("La API devolvió universo vacío — ¿headers/token bien?")

    hasta = date.today()
    desde = hasta - timedelta(days=DIAS_ATRAS)
    print(f"Backfill cierres offshore {desde} -> {hasta}  ({len(universo)} bonos)\n")

    total_filas = 0
    for u in universo:
        ric, bono = u["ric"], u["bono"]
        try:
            df = ek.get_timeseries(
                ric,
                fields="CLOSE",
                start_date=desde.isoformat(),
                end_date=hasta.isoformat(),
                interval="daily",
            )
        except Exception as e:  # backfill tolerante: un RIC que falle no corta el resto
            print(f"── {bono:5} {ric:16} ERROR: {type(e).__name__}: {e}")
            continue

        if df is None or len(df) == 0:
            print(f"── {bono:5} {ric:16} VACÍO — sin serie")
            continue

        docs = []
        for idx, row in df.iterrows():
            val = row.get("CLOSE")
            if val is None or pd.isna(val):
                continue
            docs.append({"ric": ric, "fecha": idx.date().isoformat(), "valor": float(val)})
        if not docs:
            print(f"── {bono:5} {ric:16} sin CLOSE válido")
            continue

        if DRY_RUN:
            print(f"── {bono:5} {ric:16} {len(docs):>3} cierres "
                  f"({docs[0]['fecha']} .. {docs[-1]['fecha']})  [DRY_RUN, no enviado]")
            total_filas += len(docs)
            continue

        resp = requests.post(
            f"{API_BASE}/api/ingest/eikon/cierres",
            json={"docs": docs},
            headers=HEADERS,
            timeout=60,
        )
        if resp.status_code != 200:
            print(f"── {bono:5} {ric:16} POST {resp.status_code}: {resp.text[:120]}")
            continue
        escritos = resp.json().get("escritos", 0)
        total_filas += escritos
        print(f"── {bono:5} {ric:16} {escritos:>3} cierres persistidos")

    modo = "leídos (DRY_RUN)" if DRY_RUN else "persistidos"
    print(f"\nTotal: {total_filas} cierres {modo}.")
    if DRY_RUN:
        print("Si la muestra está OK, poné DRY_RUN = False arriba y volvé a correr.")
    else:
        print("Listo — revisá la watchlist (sección ARGENTINA): 7D/MTD/YTD deberían llenarse.")


if __name__ == "__main__":
    main()
