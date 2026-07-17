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

# Campo Eikon → nombre en el payload (TODOS validados en vivo 2026-07-16 contra
# RKLB.O). CF_*/AFTMKT/PREMKT son live; los TR.* son EOD (cambian 1 vez por día,
# sirven para los retornos por período). PRIMACT_1 es el last de FUTUROS
# (fallback) — Eikon avisa "Field not found" por el que no aplique: es normal.
CAMPOS = {
    "CF_LAST":           "last",        # último precio negociado
    "CF_BID":            "bid",         # compra
    "CF_ASK":            "ask",         # venta
    "CF_HIGH":           "high",        # máximo del día
    "CF_LOW":            "low",         # mínimo del día
    "CF_CLOSE":          "prev_close",  # cierre anterior
    "CF_VOLUME":         "volumen",     # volumen del día
    "PCTCHNG":           "var_pct",     # variación % del día
    "NETCHNG_1":         "var_neta",    # cambio neto en precio del día
    "AFTMKT_PRC":        "ah_last",     # precio AFTER MARKET (post-cierre)
    "AFTMKT_VOL":        "ah_vol",      # volumen del after
    "PREMKT_PRC":        "pre_last",    # precio PRE MARKET
    "TR.PriceClose":     "eod_close",   # EOD rueda anterior
    "TR.PriceOpen":      "eod_open",
    "TR.PriceHigh":      "eod_high",
    "TR.PriceLow":       "eod_low",
    "TR.Volume":         "eod_vol",
    "TR.PricePctChg1D":  "ret_1d",      # retornos por período (EOD)
    "TR.PricePctChg5D":  "ret_5d",
    "TR.PricePctChgWTD": "ret_wtd",
    "TR.PricePctChgMTD": "ret_mtd",
    "TR.PricePctChgQTD": "ret_qtd",
    "TR.PricePctChgYTD": "ret_ytd",
    "TR.PricePctChg1M":  "ret_1m",
    "TR.PricePctChg3M":  "ret_3m",
    "TR.PricePctChg1Y":  "ret_1y",
    "TR.PricePctChg5Y":  "ret_5y",
}
FIELDS = [*CAMPOS, "PRIMACT_1"]

# ── Fundamentals para la FICHA de empresa (1 pull por día; validados en vivo
#    2026-07-17 contra AAPL/RKLB — ver docs/INTEGRACION_REUTERS.md) ──────────
FUND_SNAPSHOT = {
    "TR.CommonName":           "nombre",
    "TR.TRBCIndustry":         "industria",
    "TR.HeadquartersCountry":  "pais",
    "TR.CompanyMarketCap":     "market_cap",        # USD (crudo)
    "TR.EV":                   "ev",
    "TR.PE":                   "pe",
    "TR.FwdPE":                "fwd_pe",
    "TR.EVToEBITDA":           "ev_ebitda",
    "TR.FwdEVToEBITDA":        "fwd_ev_ebitda",
    "TR.EVToEBIT":             "ev_ebit",
    "TR.PriceToBVPerShare":    "p_bv",
    "TR.GrossMargin":          "margen_bruto",      # %
    "TR.OperatingMargin":      "margen_operativo",
    "TR.NetProfitMargin":      "margen_neto",
    "TR.NetDebtToEBITDA":      "deuda_neta_ebitda",
    "TR.CurrentRatio":         "current_ratio",
    "TR.QuickRatio":           "quick_ratio",
    "TR.TotalDebt":            "deuda_total",       # USD (crudo)
    "TR.CashAndEquivalents":   "caja",
    "TR.DividendYield":        "div_yield",         # %
    "TR.Price52WeekHigh":      "max_52s",
    "TR.Price52WeekLow":       "min_52s",
    "TR.ExpectedReportDate":   "proximo_balance",
    "TR.SharesOutstanding":    "acciones",
}
FUND_FY0 = {                    # último año fiscal, MILLONES de USD (Scale=6)
    "TR.Revenue":              "revenue",
    "TR.GrossProfit":          "gross_profit",
    "TR.EBITDA":               "ebitda",
    "TR.OperatingIncome":      "ebit",
    "TR.NetIncome":            "net_income",
    "TR.FreeCashFlow":         "fcf",
    "TR.CapitalExpenditures":  "capex",
}
FUND_SERIE = ["TR.Revenue.date", "TR.Revenue", "TR.EBITDA", "TR.NetIncome",
              "TR.FreeCashFlow"]   # 5 años fiscales, millones USD
FUND_CADA_SEG = 24 * 3600


def actualizar_precios(universo):
    ric_a_ticker = {u["ric"]: u["ticker"] for u in universo}
    try:
        # field_name=True → columnas con el nombre del campo pedido (en MAYÚSCULA),
        # no el display name ("5-day Price PCT Change") que cambia y no se puede mapear.
        data, err = ek.get_data(list(ric_a_ticker), FIELDS, field_name=True)
        if err and not ultimo:   # solo la primera pasada, para no spammear
            # PRIMACT_1 es el fallback de futuros: que falte en acciones es lo
            # esperado — se filtra. Lo que queda son problemas REALES (ej. un
            # RIC mal cargado: "record could not be found").
            relevantes = [e for e in err if "PRIMACT_1" not in str(e.get("message", ""))]
            if relevantes:
                print(f"⚠️ avisos de Eikon en la 1ra pasada ({len(relevantes)}):")
                vistos = set()
                for e in relevantes:
                    msg = str(e.get("message", ""))[:110]
                    if msg not in vistos:
                        vistos.add(msg)
                        print(f"   {msg}")
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
                v = row.get(campo.upper())
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


def _num_o_texto(v):
    """Valor de Eikon → tipo JSON-serializable (NaN→None, numpy→nativo, str→str)."""
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    if isinstance(v, str):
        return v
    try:
        return v.item()          # numpy scalar → python nativo
    except AttributeError:
        return float(v)


def actualizar_fundamentals(universo):
    """Pull DIARIO de fundamentals para la ficha: 3 llamadas (snapshot, año
    fiscal FY0 en millones USD, serie 5 años) → POST /eikon/fundamentals.
    Si falla, avisa y sigue — nunca voltea el feed de precios."""
    ric_a_ticker = {u["ric"]: u["ticker"] for u in universo}
    rics = list(ric_a_ticker)
    docs: dict[str, dict] = {}

    def doc_de(ric):
        return docs.setdefault(ric, {"ticker": ric_a_ticker[ric], "ric": ric})

    print(f"[fundamentals] bajando {len(rics)} fichas (1 vez por día)…")
    # 1) snapshot (valuación / márgenes / salud / consenso / perfil)
    df, _err = ek.get_data(rics, list(FUND_SNAPSHOT), field_name=True)
    for _, row in df.iterrows():
        ric = row["Instrument"]
        if ric not in ric_a_ticker:
            continue
        d = doc_de(ric)
        for campo, nombre in FUND_SNAPSHOT.items():
            d[nombre] = _num_o_texto(row.get(campo.upper()))
    # 2) resultados del último año fiscal (millones de USD)
    df, _err = ek.get_data(rics, list(FUND_FY0), field_name=True,
                           parameters={"Period": "FY0", "Scale": "6", "Curn": "USD"})
    for _, row in df.iterrows():
        ric = row["Instrument"]
        if ric not in ric_a_ticker:
            continue
        d = doc_de(ric)
        for campo, nombre in FUND_FY0.items():
            d[nombre] = _num_o_texto(row.get(campo.upper()))
    # 3) serie 5 años (revenue/ebitda/net income/fcf por año fiscal)
    df, _err = ek.get_data(rics, FUND_SERIE, field_name=True,
                           parameters={"SDate": "0", "EDate": "-4",
                                       "Scale": "6", "Curn": "USD"})
    for _, row in df.iterrows():
        ric = row["Instrument"]
        if ric not in ric_a_ticker:
            continue
        fecha = _num_o_texto(row.get("TR.REVENUE.DATE"))
        if not fecha:
            continue
        d = doc_de(ric)
        d.setdefault("serie_anual", []).append({
            "fecha":      str(fecha)[:10],
            "revenue":    _num_o_texto(row.get("TR.REVENUE")),
            "ebitda":     _num_o_texto(row.get("TR.EBITDA")),
            "net_income": _num_o_texto(row.get("TR.NETINCOME")),
            "fcf":        _num_o_texto(row.get("TR.FREECASHFLOW")),
        })

    lista = list(docs.values())
    if not lista:
        print("[fundamentals] ⚠️ Eikon no devolvió nada — reintento en el próximo ciclo.")
        return False
    r = requests.post(f"{API_BASE}/api/ingest/eikon/fundamentals",
                      json={"docs": lista}, headers=HEADERS, timeout=60)
    if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
        print(f"[fundamentals] ✅ {len(lista)} fichas actualizadas.")
        return True
    print(f"[fundamentals] ❌ API {r.status_code}: {r.text[:200]}")
    return False


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

    ultima_fund = 0.0
    while True:
        actualizar_precios(universo)
        if time.time() - ultima_fund > FUND_CADA_SEG:
            try:
                if actualizar_fundamentals(universo):
                    ultima_fund = time.time()
            except Exception as e:
                print(f"[fundamentals] ❌ {type(e).__name__}: {e} — sigo con precios.")
                ultima_fund = time.time() - FUND_CADA_SEG + 1800   # reintenta en 30 min
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
