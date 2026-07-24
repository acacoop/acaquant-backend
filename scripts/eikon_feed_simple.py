"""eikon_feed_simple.py — versión MÍNIMA, calcada del script de commodities.

Solo hace esto, nada más:
  1. Pide a la API la lista de RICs ya cargados (los sin RIC quedan afuera)
     + el universo de futuros CBOT (CHICAGO, ver abajo).
  2. Loop: ek.get_data(rics, CAMPOS) → POST a la API de last/bid/ask/high/low/
     cierre anterior/volumen/var% (ver CAMPOS abajo). En el mismo loop,
     los futuros de Chicago (CAMPOS_CHICAGO) → POST /eikon/chicago/quotes.

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
# Series de 5 años fiscales, en DOS llamadas (Scale=6 solo aplica a montos):
#   A) monetarios (millones USD) — negocio + salud
#   B) márgenes (%) — históricos REALES (verificado); los múltiplos (PE etc.)
#      NO se piden como serie: Eikon devuelve precio de HOY / resultados de
#      cada año, no el múltiplo que se pagaba entonces (verificado 2026-07-17).
FUND_SERIE_USD = {
    "TR.Revenue":            "revenue",
    "TR.EBITDA":             "ebitda",
    "TR.NetIncome":          "net_income",
    "TR.FreeCashFlow":       "fcf",
    "TR.TotalDebt":          "deuda",
    "TR.CashAndEquivalents": "caja",
}
FUND_SERIE_PCT = {
    "TR.GrossMargin":        "margen_bruto",
    "TR.OperatingMargin":    "margen_operativo",
    "TR.NetProfitMargin":    "margen_neto",
}
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


# ── CHICAGO (futuros CBOT → AGRO → tab CHICAGO) ──────────────────────────────
# RICs de continuación (Sc1, BOc4, …): los da la API (/eikon/chicago/universo,
# constante core/eikon_chicago.py::FAMILIAS). Se mandan valores CRUDOS — los
# factores a USD/tonelada los aplica el server al leer. PRIMACT_1 es el last
# de FUTUROS (validado en el script de commodities original).
CAMPOS_CHICAGO = {
    "CONTR_MNTH": "mes",       # mes del contrato (ej. 'JUL6')
    "PRIMACT_1":  "last",      # último precio del futuro (crudo, ¢/bu etc.)
    "SEC_ACT_1":  "var_neta",  # variación neta del día (misma unidad)
}
_chicago_primera = {"hecha": False}   # para loguear avisos solo la 1ra pasada


def actualizar_chicago(rics):
    """Un get_data para TODOS los RICs CBOT → POST /eikon/chicago/quotes.
    Llamada separada de las acciones (los campos difieren y los logs quedan
    limpios). SIN cache-diff a propósito: manda las ~25 filas en CADA loop
    como HEARTBEAT — el semáforo EN LÍNEA de la vista se prende si el último
    POST tiene <60s. Cualquier error acá NUNCA voltea el loop de precios."""
    try:
        data, err = ek.get_data(rics, list(CAMPOS_CHICAGO), field_name=True)
        if err and not _chicago_primera["hecha"]:
            vistos = set()
            for e in err:
                msg = str(e.get("message", ""))[:110]
                if msg not in vistos:
                    vistos.add(msg)
                    print(f"   [chicago] aviso Eikon: {msg}")
        _chicago_primera["hecha"] = True
        if data is None or data.empty:
            print("⚠️ [chicago] get_data no devolvió datos.")
            return

        docs = []
        for _, row in data.iterrows():
            doc = {"ric": row["Instrument"]}
            for campo, nombre in CAMPOS_CHICAGO.items():
                v = row.get(campo.upper())
                if nombre == "mes":
                    doc[nombre] = str(v) if v is not None and not pd.isna(v) else None
                else:
                    doc[nombre] = float(v) if v is not None and not pd.isna(v) else None
            if doc["last"] is None:                    # sin precio no se manda
                continue
            docs.append(doc)

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not docs:
            print(f"[{now}] ⚠️ [chicago] ningún RIC con precio.")
            return
        if DRY_RUN:
            for d in docs[:10]:
                print("  [chicago]", d)
            print(f"[{now}] [chicago] DRY_RUN — {len(docs)} leídos.")
            return
        r = requests.post(f"{API_BASE}/api/ingest/eikon/chicago/quotes",
                          json={"docs": docs}, headers=HEADERS, timeout=15)
        if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
            print(f"[{now}] ✅ [chicago] {len(docs)} futuros (heartbeat).")
        else:
            print(f"[{now}] ❌ [chicago] API {r.status_code}: {r.text[:200]}")

    except Exception as e:
        if "429" in str(e):
            print("⚠️ [chicago] límite de requests. Esperando 60s.")
            time.sleep(60)
        else:
            print("❌ [chicago] error:", type(e).__name__, e)


# ── BONOS OFF (soberanos ARG offshore → watchlist HOME + briefing) ──────────
# RICs "=1M" (páginas contribuidas MarketAxess): los da la API
# (/eikon/bonos/universo, constante core/eikon_bonos.py::BONOS_OFF). Qué campos
# publican esas páginas no está validado en vivo → set tolerante; el server
# resuelve el precio con fallback last → primact → mid(bid,ask). Los avisos
# "Field not found" de la 1ra pasada dicen qué campo no existe (normal).
CAMPOS_BONOS = {
    "CF_LAST":    "last",
    "PRIMACT_1":  "primact",
    "CF_BID":     "bid",
    "CF_ASK":     "ask",
    "CF_CLOSE":   "prev_close",
    "PCTCHNG":    "var_pct",
    "NETCHNG_1":  "var_neta",
}
_bonos_primera = {"hecha": False}


def actualizar_bonos(rics):
    """Un get_data para los soberanos offshore → POST /eikon/bonos/quotes.
    Igual que Chicago: SIN cache-diff (heartbeat) y sus errores NUNCA voltean
    el loop de precios."""
    try:
        data, err = ek.get_data(rics, list(CAMPOS_BONOS), field_name=True)
        if err and not _bonos_primera["hecha"]:
            vistos = set()
            for e in err:
                msg = str(e.get("message", ""))[:110]
                if msg not in vistos:
                    vistos.add(msg)
                    print(f"   [bonos] aviso Eikon: {msg}")
        _bonos_primera["hecha"] = True
        if data is None or data.empty:
            print("⚠️ [bonos] get_data no devolvió datos.")
            return

        docs = []
        for _, row in data.iterrows():
            doc = {"ric": row["Instrument"]}
            for campo, nombre in CAMPOS_BONOS.items():
                v = row.get(campo.upper())
                doc[nombre] = float(v) if v is not None and not pd.isna(v) else None
            # Sin NINGUNA pata de precio no se manda (page vacía / RIC muerto).
            if doc["last"] is None and doc["primact"] is None \
                    and doc["bid"] is None and doc["ask"] is None:
                continue
            docs.append(doc)

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not docs:
            print(f"[{now}] ⚠️ [bonos] ningún RIC con precio.")
            return
        if DRY_RUN:
            for d in docs[:11]:
                print("  [bonos]", d)
            print(f"[{now}] [bonos] DRY_RUN — {len(docs)} leídos.")
            return
        r = requests.post(f"{API_BASE}/api/ingest/eikon/bonos/quotes",
                          json={"docs": docs}, headers=HEADERS, timeout=15)
        if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
            print(f"[{now}] ✅ [bonos] {len(docs)} soberanos off (heartbeat).")
        else:
            print(f"[{now}] ❌ [bonos] API {r.status_code}: {r.text[:200]}")

    except Exception as e:
        if "429" in str(e):
            print("⚠️ [bonos] límite de requests. Esperando 60s.")
            time.sleep(60)
        else:
            print("❌ [bonos] error:", type(e).__name__, e)


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
    # 3) series históricas — anual (5 FY) y trimestral (8 FQ, Period=FQ0+Frq=FQ,
    #    validado 2026-07-17: sin Period=FQ0 devuelve el dato ANUAL repetido).
    #    Cada una en dos llamadas (montos Scale=6 USD / márgenes sin Scale)
    #    mergeadas por (ric, fecha).
    def bajar_serie(clave: str, params_base: dict):
        series: dict[tuple, dict] = {}   # (ric, fecha) → {campo: valor}

        def sumar(campos: dict, params: dict):
            df, _e = ek.get_data(rics, ["TR.Revenue.date", *campos], field_name=True,
                                 parameters=params)
            for _, row in df.iterrows():
                ric = row["Instrument"]
                fecha = _num_o_texto(row.get("TR.REVENUE.DATE"))
                if ric not in ric_a_ticker or not fecha:
                    continue
                fila = series.setdefault((ric, str(fecha)[:10]), {})
                for campo, nombre in campos.items():
                    v = _num_o_texto(row.get(campo.upper()))
                    if v is not None:
                        fila[nombre] = v

        sumar(FUND_SERIE_USD, {**params_base, "Scale": "6", "Curn": "USD"})
        sumar(FUND_SERIE_PCT, params_base)
        for (ric, fecha), fila in sorted(series.items(),
                                         key=lambda kv: (kv[0][0], kv[0][1]),
                                         reverse=True):
            doc_de(ric).setdefault(clave, []).append({"fecha": fecha, **fila})

    bajar_serie("serie_anual", {"SDate": "0", "EDate": "-4"})
    bajar_serie("serie_trimestral", {"SDate": "0", "EDate": "-7",
                                     "Period": "FQ0", "Frq": "FQ"})

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

    # Grupos extra (universo constante del server). Tolerante — si la API vieja
    # no tiene el endpoint, sigue sin ese grupo.
    def _universo_extra(nombre, path, key):
        try:
            rc = requests.get(f"{API_BASE}{path}", headers=HEADERS, timeout=15)
            if rc.status_code == 200 and "json" in rc.headers.get("content-type", ""):
                rics = [u["ric"] for u in rc.json()["universo"] if u.get("ric")]
                print(f"[{nombre}] suscribiendo {len(rics)} {key}.")
                return rics
            print(f"[{nombre}] API {rc.status_code} — sigo sin este grupo.")
        except Exception as e:
            print(f"[{nombre}] {type(e).__name__}: {e} — sigo sin este grupo.")
        return []

    rics_chicago = _universo_extra("chicago", "/api/ingest/eikon/chicago/universo",
                                   "futuros CBOT")
    rics_bonos = _universo_extra("bonos", "/api/ingest/eikon/bonos/universo",
                                 "soberanos offshore")

    ultima_fund = 0.0
    while True:
        actualizar_precios(universo)
        if rics_chicago:
            actualizar_chicago(rics_chicago)
        if rics_bonos:
            actualizar_bonos(rics_bonos)
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
