"""eikon_feed.py — feed LIVE del subyacente US de cada CEDEAR (PC de oficina).

Mismo patrón que el feed del dólar MAE: este script corre en la PC de la oficina
con **Eikon/LSEG Workspace ABIERTO y logueado** (la Desktop Session no funciona
sin la app), pollea Eikon cada INTERVALO_SEG y le pega a la API — la PC no toca
la base. Del lado del server: `api/routers/ingest.py` → `core/eikon_live.py` →
SQL `mercado.eikon_snapshot` (PRUEBA — de momento nadie lee esa tabla).

Flujo por arranque:
  1. GET  /api/ingest/eikon/universo   → underlyings activos + RIC si lo tienen.
  2. Los que NO tienen RIC se resuelven acá con symbology de Eikon
     (`ek.get_symbology(ticker → RIC)`) y se persisten con POST /eikon/rics
     (el server SOLO llena vacíos: lo cargado a mano en Manager no se pisa).
  3. Loop: `ek.get_data(rics, FIELDS)` → POST /eikon/quotes SOLO de lo que cambió.

Uso (en la PC de oficina, con las 3 constantes de abajo pegadas):
    python eikon_feed.py                     # feed continuo (Ctrl+C para cortar)
    python eikon_feed.py --once              # una sola pasada (para probar)
    python eikon_feed.py --once --dry-run    # muestra la data y NO postea nada
    python eikon_feed.py --tickers AAPL,NVDA # subset del universo (prueba chica)
    python eikon_feed.py --intervalo 30      # override del intervalo (seg)

⚠️ Los CAMPOS de Eikon (CF_*) son los candidatos estándar de real-time para
equities — validalos con `--once --dry-run` en la primera corrida: si alguno
viene vacío/NaN para todos, se ajusta acá y listo.

Es standalone a propósito (no importa `core/`): en la PC de oficina solo hace
falta `pip install eikon requests`.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

import eikon as ek
import requests

# ── Config: pegá tus valores ACÁ (no commitearlos — quedan solo en la PC) ────
EIKON_APP_KEY = "PEGA_TU_APP_KEY_ACA"
API_BASE      = "https://api.acaquant.com"
INGEST_TOKEN  = "PEGA_EL_X_INGEST_TOKEN_ACA"          # el mismo del feed MAE
CF_CLIENT_ID  = ""                                     # service token CF Access
CF_SECRET     = ""                                     #   (vacíos si no aplica)

INTERVALO_SEG = 20
CHUNK_RICS    = 100          # RICs por llamada a get_data (no saturar Eikon)

# Campos real-time candidatos (validar con --once --dry-run) → nombre en payload.
FIELDS = {
    "CF_LAST":   "last",       # último precio
    "PCTCHNG":   "var_pct",    # variación % del día
    "CF_BID":    "bid",
    "CF_ASK":    "ask",
    "CF_OPEN":   "open",
    "CF_HIGH":   "high",
    "CF_LOW":    "low",
    "CF_CLOSE":  "prev_close",
    "CF_VOLUME": "volumen",
    "CF_TIME":   "hora",       # hora del último trade (exchange)
}


def _headers() -> dict:
    h = {"X-Ingest-Token": INGEST_TOKEN}
    if CF_CLIENT_ID and CF_SECRET:
        h["CF-Access-Client-Id"] = CF_CLIENT_ID
        h["CF-Access-Client-Secret"] = CF_SECRET
    return h


def _log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def traer_universo() -> list[dict]:
    r = requests.get(f"{API_BASE}/api/ingest/eikon/universo", headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()["universo"]


def resolver_rics_faltantes(universo: list[dict], dry_run: bool) -> list[dict]:
    """Resuelve por symbology los underlyings sin RIC y los persiste en el server.
    Devuelve el universo con los RICs completados (los irresolubles quedan afuera)."""
    faltantes = [u["ticker"] for u in universo if not u.get("ric")]
    if not faltantes:
        return universo

    _log(f"symbology: resolviendo RIC de {len(faltantes)} underlyings sin cargar…")
    resueltos: dict[str, str] = {}
    for i in range(0, len(faltantes), CHUNK_RICS):
        chunk = faltantes[i:i + CHUNK_RICS]
        try:
            df = ek.get_symbology(chunk, from_symbol_type="ticker",
                                  to_symbol_type="RIC", best_match=True)
        except Exception as e:
            _log(f"⚠️ symbology falló para un chunk de {len(chunk)}: {e}")
            continue
        for ticker, row in df.iterrows():
            ric = row.get("RIC")
            if isinstance(ric, str) and ric.strip():
                resueltos[str(ticker).upper()] = ric.strip()

    sin_resolver = sorted(set(faltantes) - set(resueltos))
    if sin_resolver:
        _log(f"⚠️ {len(sin_resolver)} sin RIC (quedan FUERA del feed; cargalos a mano "
             f"en Manager → TÍTULOS → RENTA VARIABLE): {', '.join(sin_resolver)}")

    if resueltos and not dry_run:
        payload = {"rics": [{"ticker": t, "ric": r} for t, r in sorted(resueltos.items())]}
        resp = requests.post(f"{API_BASE}/api/ingest/eikon/rics",
                             headers=_headers(), json=payload, timeout=30)
        resp.raise_for_status()
        _log(f"✅ {resp.json().get('actualizados')} RICs persistidos en el catálogo.")
    elif resueltos:
        _log(f"(dry-run) {len(resueltos)} RICs resueltos, NO persistidos.")

    out = []
    for u in universo:
        ric = u.get("ric") or resueltos.get(u["ticker"])
        if ric:
            out.append({"ticker": u["ticker"], "ric": ric})
    return out


def leer_quotes(universo: list[dict]) -> list[dict]:
    """Una pasada de ek.get_data sobre todos los RICs → lista de docs para la API."""
    ric_a_ticker = {u["ric"]: u["ticker"] for u in universo}
    rics = list(ric_a_ticker)
    docs: list[dict] = []
    for i in range(0, len(rics), CHUNK_RICS):
        chunk = rics[i:i + CHUNK_RICS]
        data, err = ek.get_data(chunk, list(FIELDS))
        if err:
            _log(f"⚠️ get_data devolvió errores (sigue con lo que vino): {err}")
        if data is None or data.empty:
            continue
        for _, row in data.iterrows():
            ric = row.get("Instrument")
            ticker = ric_a_ticker.get(ric)
            if not ticker:
                continue
            doc: dict = {"ticker": ticker, "ric": ric}
            for campo_ek, campo in FIELDS.items():
                doc[campo] = _jsonable(row.get(campo_ek))
            docs.append(doc)
    return docs


def _jsonable(v):
    """Escalar de pandas/numpy → tipo nativo JSON-serializable (NaN → None).
    json.dumps no banca numpy.int64/Timestamp — hay que bajarlos a nativo."""
    if v is None:
        return None
    try:
        if v != v:               # NaN/NaT
            return None
    except Exception:
        pass
    if isinstance(v, (bool, int, float, str)):
        return v
    try:
        return v.item()          # numpy scalar → python nativo
    except AttributeError:
        return str(v)


def postear_quotes(docs: list[dict]) -> int:
    r = requests.post(f"{API_BASE}/api/ingest/eikon/quotes",
                      headers=_headers(), json={"docs": docs}, timeout=30)
    r.raise_for_status()
    return r.json().get("escritos", 0)


def main() -> None:
    ap = argparse.ArgumentParser(description="Feed Eikon → acaquant (subyacentes de CEDEARs)")
    ap.add_argument("--once", action="store_true", help="una sola pasada y sale")
    ap.add_argument("--dry-run", action="store_true", help="no postea nada (solo muestra)")
    ap.add_argument("--tickers", help="subset separado por coma (ej. AAPL,NVDA)")
    ap.add_argument("--intervalo", type=int, default=INTERVALO_SEG, help="segundos entre pasadas")
    args = ap.parse_args()

    if "PEGA_TU" in EIKON_APP_KEY or "PEGA_EL" in INGEST_TOKEN:
        sys.exit("Pegá EIKON_APP_KEY e INGEST_TOKEN en las constantes del script antes de correr.")

    ek.set_app_key(EIKON_APP_KEY)

    universo = traer_universo()
    if args.tickers:
        subset = {t.strip().upper() for t in args.tickers.split(",") if t.strip()}
        universo = [u for u in universo if u["ticker"] in subset]
    _log(f"universo: {len(universo)} underlyings")
    universo = resolver_rics_faltantes(universo, dry_run=args.dry_run)
    if not universo:
        sys.exit("Sin RICs para suscribir — nada que hacer.")
    _log(f"suscribiendo {len(universo)} RICs, intervalo {args.intervalo}s")

    ultimo: dict[str, dict] = {}     # cache: solo se postea lo que cambió
    while True:
        try:
            docs = leer_quotes(universo)
            cambiados = [d for d in docs if ultimo.get(d["ticker"]) != d]
            if args.dry_run:
                for d in docs[:10]:
                    _log(f"  {d}")
                _log(f"(dry-run) {len(docs)} quotes leídos, {len(cambiados)} con cambios.")
            elif cambiados:
                n = postear_quotes(cambiados)
                _log(f"✅ {n} quotes actualizados ({len(docs) - len(cambiados)} sin cambios).")
            else:
                _log("🔄 sin cambios.")
            for d in docs:
                ultimo[d["ticker"]] = d
        except Exception as e:
            if "429" in str(e):
                _log("⚠️ rate limit de Eikon (429) — espero 60s.")
                time.sleep(60)
            else:
                _log(f"❌ error (reintento en el próximo ciclo): {e}")

        if args.once:
            return
        time.sleep(args.intervalo)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _log("cortado por teclado — chau.")
