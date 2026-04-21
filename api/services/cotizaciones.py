"""Capa de servicio — cotizaciones.

Funciones puras (sin FastAPI, sin HTTP) que hacen el trabajo de acceso a
Mongo + transformación. Llamadas desde:

1. `api/routers/cotizaciones.py` — handlers FastAPI (thin wrappers).
2. `api/agent/tools.py::dispatch` — llamada directa, sin loopback HTTP.

Beneficios vs. llamada HTTP loopback:
- ~100-300ms menos por turn del asistente (sin DNS localhost + socket +
  re-parse FastAPI + verify_api_key + re-serialize JSON).
- Tests deterministas del agente sin levantar uvicorn.
- Errores llegan como excepciones tipadas, no como status codes.

El `@cached(ttl=N)` vive acá: así ambos callers (router + dispatch directo)
comparten el cache hit.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from api.cache import cached
from api.db import get_db_opciones, get_db_trading, get_db_valuaciones
from core.mongo import get_mongo_client


def _ticker_filter(instrumento: str) -> dict:
    """Regex substring escape — compat con ticker corto o completo."""
    return {"$regex": re.escape(instrumento), "$options": "i"}


def resolver_ticker_exacto(instrumento: str) -> str | None:
    """Resuelve un ticker (corto o completo) al ticker completo de ROFEX.

    Evita regex table-scan cuando se puede usar match exacto (índice
    (ticker, timestamp) en TimeSales). Si el input ya trae ' - ', se asume
    completo. Si es corto, se busca en Trading.Curvas.ticker_corto.

    Devuelve el ticker completo o None si no se pudo resolver.
    """
    instr = (instrumento or "").strip()
    if not instr:
        return None
    if " - " in instr:
        return instr
    db = get_db_trading()
    doc = db["Curvas"].find_one(
        {"ticker_corto": instr}, {"ticker": 1, "_id": 0}
    )
    return doc.get("ticker") if doc else None


# ─────────────────────────────────────────────────────────────────────────────
# Series BCRA (BADLAR, CER, DOLAR)
# ─────────────────────────────────────────────────────────────────────────────


def _query_serie(collection: str, desde: str | None, hasta: str | None) -> list:
    db = get_db_trading()
    filtro: dict = {}
    if desde or hasta:
        rango: dict = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        filtro["fecha"] = rango
    return list(
        db[collection]
        .find(filtro, {"_id": 0, "fecha": 1, "valor": 1})
        .sort("fecha", 1)
    )


@cached(ttl=3600)
def get_badlar(desde: str | None = None, hasta: str | None = None) -> list:
    return _query_serie("BADLAR", desde, hasta)


@cached(ttl=3600)
def get_cer(desde: str | None = None, hasta: str | None = None) -> list:
    return _query_serie("CER", desde, hasta)


@cached(ttl=3600)
def get_dolar(desde: str | None = None, hasta: str | None = None) -> list:
    return _query_serie("DOLAR", desde, hasta)


# ─────────────────────────────────────────────────────────────────────────────
# Dólar MEP
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=5)
def get_ultimo_mep() -> dict:
    """Último valor de dólar MEP/CCL/canje. Prefiere snapshot live (5s
    desde engines/dolares.py vía WS) y cae al último doc del cron de cierre
    (Valuaciones.Dolar) si el snapshot live no existe.

    Devuelve {mep, ccl, canje, timestamp, source}. Los 3 campos numéricos
    pueden ser None si los inputs WS no están disponibles."""
    db = get_db_valuaciones()

    snap = db["DolarSnapshot"].find_one(
        {"_id": "current"},
        {"_id": 0, "mep": 1, "ccl": 1, "canje": 1, "timestamp": 1, "source": 1},
    )
    if snap and snap.get("mep") is not None:
        return snap

    # Fallback: último cron de cierre. No tiene 'source' → marcamos.
    doc = db["Dolar"].find_one(
        {}, {"_id": 0, "mep": 1, "ccl": 1, "canje": 1, "timestamp": 1},
        sort=[("timestamp", -1)],
    )
    if doc:
        doc["source"] = "cron_close"
    return doc or {}


@cached(ttl=300)
def get_historico_mep(desde: str | None = None, hasta: str | None = None) -> list:
    """Serie histórica del dólar MEP (Valuaciones.Dolar)."""
    db = get_db_valuaciones()
    filtro: dict = {}
    if desde or hasta:
        rango: dict = {}
        if desde:
            rango["$gte"] = datetime.fromisoformat(desde)
        if hasta:
            rango["$lte"] = datetime.fromisoformat(hasta + "T23:59:59")
        filtro["timestamp"] = rango
    return list(db["Dolar"].find(filtro, {"_id": 0, "mep": 1, "timestamp": 1}))


# ─────────────────────────────────────────────────────────────────────────────
# Caución (escrita por engines/caucion.py — snapshot live + cierre histórico)
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=5)
def get_caucion(moneda: str | None = None) -> list:
    """Snapshot live de caución (Trading.CaucionSnapshot).

    Devuelve 1 o 2 docs: con `moneda` filtra solo esa, sin filtro devuelve ambas.
    Cada doc trae TNA last/bid/offer/open/high/low/closing + plazo_dias + ticker.
    """
    db = get_db_trading()
    filtro: dict = {}
    if moneda:
        filtro["moneda"] = moneda.upper()
    return list(db["CaucionSnapshot"].find(filtro, {"_id": 0}))


@cached(ttl=300)
def get_historico_caucion(
    moneda: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
) -> list:
    """Serie histórica de caución cierre diario (Trading.Caucion)."""
    db = get_db_trading()
    filtro: dict = {}
    if moneda:
        filtro["moneda"] = moneda.upper()
    if desde or hasta:
        rango: dict = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        filtro["fecha"] = rango
    return list(
        db["Caucion"]
        .find(filtro, {"_id": 0})
        .sort([("fecha", 1), ("moneda", 1)])
    )


# ─────────────────────────────────────────────────────────────────────────────
# Futuros DLR (escrita por engines/futuros_dlr.py — outrights DLR/MMMYY)
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=5)
def get_futuros_dlr() -> list:
    """Snapshot live de outrights DLR (Trading.FuturosDLRSnapshot).
    Devuelve la curva entera ordenada por vencimiento ascendente."""
    db = get_db_trading()
    out = list(db["FuturosDLRSnapshot"].find({}, {"_id": 0}).sort("vencimiento", 1))
    return out


@cached(ttl=300)
def get_historico_futuros_dlr(
    ticker: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
) -> list:
    """Cierre histórico (Trading.FuturosDLR). Filtrable por ticker y rango."""
    db = get_db_trading()
    filtro: dict = {}
    if ticker:
        filtro["ticker"] = ticker
    if desde or hasta:
        rango: dict = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        filtro["fecha"] = rango
    return list(
        db["FuturosDLR"]
        .find(filtro, {"_id": 0})
        .sort([("fecha", 1), ("vencimiento", 1)])
    )


# ─────────────────────────────────────────────────────────────────────────────
# Forwards
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=30)
def get_forwards(curva: str | None = None) -> list:
    db = get_db_trading()
    filtro: dict = {}
    if curva:
        filtro["curva"] = curva
    return list(db["ForwardsLive"].find(filtro, {"_id": 0}))


@cached(ttl=300)
def get_historico_forwards(
    curva: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
) -> list:
    db = get_db_trading()
    filtro: dict = {}
    if curva:
        filtro["curva"] = curva
    if desde or hasta:
        rango: dict = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        filtro["fecha"] = rango
    return list(db["ForwardsHistorico"].find(filtro, {"_id": 0}))


# ─────────────────────────────────────────────────────────────────────────────
# Breakevens
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=30)
def get_breakevens() -> list:
    db = get_db_trading()
    return list(db["BreakevensLive"].find({}, {"_id": 0}))


@cached(ttl=300)
def get_historico_breakevens(desde: str | None = None, hasta: str | None = None) -> list:
    db = get_db_trading()
    filtro: dict = {}
    if desde or hasta:
        rango: dict = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        filtro["fecha"] = rango
    return list(db["BreakevensHistorico"].find(filtro, {"_id": 0}))


# ─────────────────────────────────────────────────────────────────────────────
# Renta Fija (MarketSnapshot)
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=5)
def get_renta_fija(instrumento: str | None = None) -> list:
    db = get_db_trading()
    filtro: dict = {}
    if instrumento:
        filtro["ticker"] = _ticker_filter(instrumento)

    pipeline = [
        {"$match": filtro},
        {"$project": {
            "_id": 0,
            "instrumento": "$ticker",
            "book": 1,
            "metrics.total_nominals": 1,
            "metrics.vwap": 1,
            "metrics.last_price": 1,
            "metrics.open_price": 1,
            "metrics.high_price": 1,
            "metrics.low_price": 1,
            "metrics.closing_price": 1,
            "recent_trades": 1,
        }},
    ]
    return list(db["MarketSnapshot"].aggregate(pipeline))


# ─────────────────────────────────────────────────────────────────────────────
# Opciones
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=10)
def get_opciones(instrumento: str | None = None, tipo: str | None = None) -> list:
    db = get_db_opciones()
    filtro: dict = {}
    if instrumento:
        filtro["symbol"] = _ticker_filter(instrumento)
    if tipo:
        filtro["tipo"] = tipo.upper()

    pipeline = [
        {"$match": filtro},
        {"$project": {
            "_id": 0,
            "instrumento": "$symbol",
            "bid": 1,
            "offer": 1,
            "last": 1,
            "open": 1,
            "high": 1,
            "low": 1,
            "ev": 1,
            "spot": 1,
            "strike": 1,
            "tipo": 1,
            "vence": 1,
            "closing_price": 1,
            "delta": 1,
            "gamma": 1,
            "iv": 1,
            "theta": 1,
            "vega": 1,
            "updated_at": 1,
        }},
    ]
    return list(db["OptionsSnapshot"].aggregate(pipeline))


@cached(ttl=60)
def get_opciones_meta() -> dict:
    """Metadata opciones: tasa libre de riesgo + VR (ADR/local)."""
    db = get_db_opciones()
    docs = list(db["Metadata"].find(
        {"type": {"$in": ["vr_ggal", "config"]}}, {"_id": 0}
    ))
    by_type = {d.get("type"): d for d in docs}
    vr = by_type.get("vr_ggal") or {}
    cfg = by_type.get("config") or {}
    return {
        "tasa": float(cfg.get("tasa") or 0.0),
        "vr_local": float(vr.get("vr_local") or 0.0),
        "vr_adr": float(vr.get("vr_adr") or 0.0),
        "updated_at": vr.get("updated_at"),
    }


@cached(ttl=30)
def get_historico_opciones(
    instrumento: str | None = None,
    tipo: str | None = None,
) -> list:
    """Trades de opciones de los últimos 21 días (Opciones.Data)."""
    db = get_db_opciones()
    corte = datetime.now() - timedelta(days=21)
    filtro: dict = {"timestamp": {"$gte": corte}}
    if instrumento:
        filtro["symbol"] = instrumento
    if tipo:
        filtro["tipo"] = tipo.upper()

    pipeline = [
        {"$match": filtro},
        {"$sort": {"timestamp": -1}},
        {"$limit": 5000},
        {"$project": {
            "_id": 0,
            "instrumento": "$symbol",
            "timestamp": 1,
            "last_timestamp": 1,
            "bid": 1,
            "offer": 1,
            "last": 1,
            "spot": 1,
            "strike": 1,
            "tipo": 1,
            "iv": 1,
            "delta": 1,
            "gamma": 1,
            "vega": 1,
            "theta": 1,
        }},
    ]
    return list(db["Data"].aggregate(pipeline))


def update_opciones_tasa(valor: float) -> dict:
    """Actualiza la tasa libre de riesgo en Opciones.Metadata.config.
    Post-update hace clear_cache() (no decorator — es mutación)."""
    from api.cache import clear_cache
    col = get_mongo_client()["Opciones"]["Metadata"]
    col.update_one(
        {"type": "config"},
        {"$set": {"tasa": float(valor)}},
        upsert=True,
    )
    clear_cache()
    return {"ok": True, "tasa": float(valor)}


# ─────────────────────────────────────────────────────────────────────────────
# Histórico de trades + curvas (Trading.TimeSales)
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=15)
def get_historico_trades(instrumento: str | None = None) -> list:
    """Trades de los últimos 15 días. Match EXACTO por ticker para usar índice."""
    db = get_db_trading()
    corte = datetime.now(UTC) - timedelta(days=15)
    filtro: dict = {"timestamp": {"$gte": corte}}
    if instrumento:
        exacto = resolver_ticker_exacto(instrumento)
        if exacto is None:
            return []
        filtro["ticker"] = exacto

    pipeline = [
        {"$match": filtro},
        {"$sort": {"timestamp": -1}},
        {"$limit": 10000},
        {"$project": {
            "_id": 0,
            "instrumento": "$ticker",
            "timestamp": 1,
            "price": 1,
            "size": 1,
            "side": 1,
            "money": 1,
            "duration": 1,
            "TEA": 1,
            "TEM": 1,
            "paridad": 1,
        }},
    ]
    return list(db["TimeSales"].aggregate(pipeline))


_CURVAS_VALIDAS = ("cer", "tasa_fija", "tamar", "soberanos", "dolar_linked")
_ORDENES_VALIDOS = ("vencimiento", "volumen_dia", "tea", "duration")


@cached(ttl=30)
def listar_curva(
    curva: str,
    ordenar_por: str = "vencimiento",
    vencimiento_min_meses: float | None = None,
    vencimiento_max_meses: float | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Lista los bonos de una curva con metadata enriquecida.

    Devuelve para cada instrumento: ticker, ticker_corto, tipo, vencimiento,
    precio, TEA/TEM, paridad, duration, volumen del día. Ordenable por
    vencimiento (default), volumen, TEA o duration. Filtrable por horizonte
    (vencimiento_min/max_meses).

    Ver docs/asistente/tools_spec.md §2.1 para contrato completo.
    """
    if curva not in _CURVAS_VALIDAS:
        return []
    if ordenar_por not in _ORDENES_VALIDOS:
        ordenar_por = "vencimiento"

    db = get_db_trading()

    # 1. Definición estática de la curva
    curva_docs = list(db["Curvas"].find(
        {"curva": curva},
        {"_id": 0, "ticker": 1, "ticker_corto": 1, "tipo": 1,
         "fecha_vencimiento": 1, "fecha_emision": 1},
    ))
    if not curva_docs:
        return []

    # 2. Filtrar por horizonte (meses al vencimiento)
    ahora = datetime.now(UTC)
    filtrados: list[dict] = []
    for d in curva_docs:
        vto_raw = d.get("fecha_vencimiento")
        if not vto_raw:
            continue
        try:
            if isinstance(vto_raw, datetime):
                vto = vto_raw if vto_raw.tzinfo else vto_raw.replace(tzinfo=UTC)
            else:
                vto = datetime.fromisoformat(str(vto_raw)[:10]).replace(tzinfo=UTC)
        except Exception:
            continue
        meses = round((vto - ahora).days / 30.44, 1)
        if vencimiento_min_meses is not None and meses < vencimiento_min_meses:
            continue
        if vencimiento_max_meses is not None and meses > vencimiento_max_meses:
            continue
        d["_meses"] = meses
        filtrados.append(d)

    if not filtrados:
        return []

    tickers = [d["ticker"] for d in filtrados]

    # 3. Último trade enriquecido por ticker (TEA/TEM/paridad/duration/convexity/price)
    enrich_map: dict[str, dict] = {}
    for r in db["TimeSales"].aggregate([
        {"$match": {"ticker": {"$in": tickers}, "price": {"$gt": 0}}},
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id": "$ticker",
            "price":     {"$first": "$price"},
            "TEA":       {"$first": "$TEA"},
            "TEM":       {"$first": "$TEM"},
            "paridad":   {"$first": "$paridad"},
            "duration":  {"$first": "$duration"},
            "convexity": {"$first": "$convexity"},
            "ts":        {"$first": "$timestamp"},
        }},
    ]):
        enrich_map[r["_id"]] = r

    # 4. Volumen del día desde MarketSnapshot
    vol_map: dict[str, dict] = {}
    for r in db["MarketSnapshot"].find(
        {"ticker": {"$in": tickers}},
        {"_id": 0, "ticker": 1, "metrics.total_money": 1, "metrics.total_nominals": 1},
    ):
        m = r.get("metrics") or {}
        vol_map[r["ticker"]] = {
            "total_money": m.get("total_money") or 0,
            "total_nominals": m.get("total_nominals") or 0,
        }

    # 5. Construir output
    out: list[dict] = []
    for d in filtrados:
        enrich = enrich_map.get(d["ticker"], {})
        vol = vol_map.get(d["ticker"], {})
        ts_last = enrich.get("ts")
        out.append({
            "ticker": d["ticker"],
            "ticker_corto": d.get("ticker_corto"),
            "tipo": d.get("tipo"),
            "fecha_vencimiento": str(d.get("fecha_vencimiento"))[:10] if d.get("fecha_vencimiento") else None,
            "fecha_emision": str(d.get("fecha_emision"))[:10] if d.get("fecha_emision") else None,
            "meses_al_vto": d["_meses"],
            "ultimo_precio": enrich.get("price"),
            "tea": enrich.get("TEA"),
            "tem": enrich.get("TEM"),
            "paridad": enrich.get("paridad"),
            "duration": enrich.get("duration"),
            "convexity": enrich.get("convexity"),
            "total_money_dia": vol.get("total_money"),
            "total_nominals_dia": vol.get("total_nominals"),
            "ts_ultimo_trade": ts_last.isoformat() if isinstance(ts_last, datetime) else ts_last,
        })

    # 6. Ordenamiento
    if ordenar_por == "vencimiento":
        out.sort(key=lambda x: x.get("fecha_vencimiento") or "9999")
    elif ordenar_por == "volumen_dia":
        out.sort(key=lambda x: -(x.get("total_money_dia") or 0))
    elif ordenar_por == "tea":
        out.sort(key=lambda x: (x.get("tea") is None, x.get("tea") or 0))
    elif ordenar_por == "duration":
        out.sort(key=lambda x: (x.get("duration") is None, x.get("duration") or 0))

    if limit and limit > 0:
        out = out[:limit]

    return out


@cached(ttl=300)
def get_historico_curva(curva: str) -> list:
    """Serie diaria por ticker de una curva: último precio + enriquecimiento."""
    db = get_db_trading()
    meta = {
        d["ticker"]: d.get("ticker_corto") or d["ticker"]
        for d in db["Curvas"].find({"curva": curva}, {"ticker": 1, "ticker_corto": 1})
    }
    if not meta:
        return []

    pipeline = [
        {"$match": {"ticker": {"$in": list(meta.keys())}, "price": {"$gt": 0}}},
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id": {
                "ticker": "$ticker",
                "fecha": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
            },
            "price": {"$first": "$price"},
            "TEA": {"$first": "$TEA"},
            "TEM": {"$first": "$TEM"},
            "duration": {"$first": "$duration"},
            "paridad": {"$first": "$paridad"},
        }},
        {"$sort": {"_id.fecha": 1, "_id.ticker": 1}},
    ]
    out = []
    for r in db["TimeSales"].aggregate(pipeline):
        t_full = r["_id"]["ticker"]
        out.append({
            "fecha": r["_id"]["fecha"],
            "ticker": meta.get(t_full, t_full),
            "price": r.get("price"),
            "TEA": r.get("TEA"),
            "TEM": r.get("TEM"),
            "duration": r.get("duration"),
            "paridad": r.get("paridad"),
        })
    return out


# ═════════════════════════════════════════════════════════════════════════════
# TIER 2 — tools de extensión sobre data existente
# ═════════════════════════════════════════════════════════════════════════════


@cached(ttl=300)
def snapshot_curva_historico(curva: str, fecha: str) -> list[dict]:
    """Curva entera tal como cerró un día pasado.

    Para cada bono de la curva, busca el último trade de ese día en
    Trading.TimeSales y devuelve el mismo shape que listar_curva() (ticker,
    ticker_corto, precio, TEA, TEM, paridad, duration, convexity).

    Si un bono no operó ese día, no aparece en el resultado (vs invents).
    """
    if curva not in _CURVAS_VALIDAS:
        return []

    db = get_db_trading()

    # 1. Tickers + metadata de la curva
    curva_docs = list(db["Curvas"].find(
        {"curva": curva},
        {"_id": 0, "ticker": 1, "ticker_corto": 1, "tipo": 1,
         "fecha_vencimiento": 1, "fecha_emision": 1},
    ))
    if not curva_docs:
        return []

    tickers = [d["ticker"] for d in curva_docs if d.get("ticker")]
    meta_by_ticker = {d["ticker"]: d for d in curva_docs if d.get("ticker")}

    # 2. Rango [fecha 00:00, fecha+1 00:00) — último trade del día pedido
    try:
        fecha_dt = datetime.fromisoformat(fecha[:10]).replace(tzinfo=UTC)
    except ValueError:
        return []
    fin_dt = fecha_dt + timedelta(days=1)

    # 3. Último trade por ticker en ese rango
    enrich: dict[str, dict] = {}
    for r in db["TimeSales"].aggregate([
        {"$match": {
            "ticker": {"$in": tickers},
            "timestamp": {"$gte": fecha_dt, "$lt": fin_dt},
            "price": {"$gt": 0},
        }},
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id":       "$ticker",
            "price":     {"$first": "$price"},
            "TEA":       {"$first": "$TEA"},
            "TEM":       {"$first": "$TEM"},
            "paridad":   {"$first": "$paridad"},
            "duration":  {"$first": "$duration"},
            "convexity": {"$first": "$convexity"},
            "ts":        {"$first": "$timestamp"},
        }},
    ]):
        enrich[r["_id"]] = r

    # 4. Salida: solo los que operaron ese día
    out: list[dict] = []
    ahora = fecha_dt  # usamos la fecha pedida como "ahora" para calcular meses al vto
    for ticker, m in meta_by_ticker.items():
        if ticker not in enrich:
            continue
        en = enrich[ticker]
        vto_raw = m.get("fecha_vencimiento")
        meses = None
        try:
            if isinstance(vto_raw, datetime):
                vto = vto_raw if vto_raw.tzinfo else vto_raw.replace(tzinfo=UTC)
            else:
                vto = datetime.fromisoformat(str(vto_raw)[:10]).replace(tzinfo=UTC)
            meses = round((vto - ahora).days / 30.44, 1)
        except Exception:
            pass
        ts_last = en.get("ts")
        out.append({
            "ticker":             ticker,
            "ticker_corto":       m.get("ticker_corto"),
            "tipo":               m.get("tipo"),
            "fecha_vencimiento":  str(m.get("fecha_vencimiento"))[:10] if m.get("fecha_vencimiento") else None,
            "meses_al_vto":       meses,
            "ultimo_precio":      en.get("price"),
            "tea":                en.get("TEA"),
            "tem":                en.get("TEM"),
            "paridad":             en.get("paridad"),
            "duration":           en.get("duration"),
            "convexity":          en.get("convexity"),
            "ts_ultimo_trade":    ts_last.isoformat() if isinstance(ts_last, datetime) else ts_last,
        })
    out.sort(key=lambda x: x.get("fecha_vencimiento") or "9999")
    return out


_METRICAS_PENDIENTE = ("tea", "tem", "duration")


@cached(ttl=60)
def calcular_pendiente_curva(
    curva: str,
    metrica: str = "tea",
    fecha_comparacion: str | None = None,
) -> dict:
    """Pendiente de una curva (valor largo − valor corto).

    "Corto" = menor duration. "Largo" = mayor duration. El resultado se
    expresa en basis points (bps). Opcionalmente compara con la curva del
    día `fecha_comparacion` y devuelve el delta de pendiente (útil para
    detectar empinamiento/aplanamiento).

    metrica ∈ {tea, tem, duration}. Default tea.
    """
    if curva not in _CURVAS_VALIDAS:
        return {"error": f"curva inválida: {curva}"}
    metrica = metrica.lower()
    if metrica not in _METRICAS_PENDIENTE:
        return {"error": f"metrica inválida: {metrica}"}

    # Pendiente actual — usa listar_curva que ya tiene la data live
    ahora = listar_curva(curva=curva, ordenar_por="duration")
    validos = [b for b in ahora if b.get(metrica) is not None and b.get("duration")]
    if len(validos) < 2:
        return {"error": "insuficientes instrumentos con metrica + duration"}

    corto_now = validos[0]
    largo_now = validos[-1]
    valor_corto_now = float(corto_now[metrica])
    valor_largo_now = float(largo_now[metrica])
    pendiente_actual_bps = round((valor_largo_now - valor_corto_now) * 10000, 0)

    out: dict = {
        "curva":                   curva,
        "metrica":                 metrica,
        "pendiente_actual_bps":    pendiente_actual_bps,
        "corto": {
            "ticker":   corto_now.get("ticker_corto") or corto_now.get("ticker"),
            "duration": corto_now.get("duration"),
            metrica:    valor_corto_now,
        },
        "largo": {
            "ticker":   largo_now.get("ticker_corto") or largo_now.get("ticker"),
            "duration": largo_now.get("duration"),
            metrica:    valor_largo_now,
        },
    }

    if fecha_comparacion:
        hist = snapshot_curva_historico(curva=curva, fecha=fecha_comparacion)
        hist_validos = [b for b in hist if b.get(metrica) is not None and b.get("duration")]
        if len(hist_validos) >= 2:
            hist_validos.sort(key=lambda b: b["duration"])
            corto_hist = hist_validos[0]
            largo_hist = hist_validos[-1]
            pend_hist_bps = round(
                (float(largo_hist[metrica]) - float(corto_hist[metrica])) * 10000, 0
            )
            delta = round(pendiente_actual_bps - pend_hist_bps, 0)
            if delta > 10:
                interpretacion = "empinamiento"
            elif delta < -10:
                interpretacion = "aplanamiento"
            else:
                interpretacion = "sin cambio material"
            out.update({
                "fecha_comparacion":          fecha_comparacion,
                "pendiente_comparacion_bps":  pend_hist_bps,
                "delta_bps":                  delta,
                "interpretacion":             interpretacion,
            })
        else:
            out["fecha_comparacion"] = fecha_comparacion
            out["error_comparacion"] = "insuficientes instrumentos en fecha pedida"

    return out


def _clasificar_liquidez(ratio: float | None) -> str:
    if ratio is None:
        return "sin_datos"
    if ratio < 0.3:
        return "baja"
    if ratio <= 1.5:
        return "media"
    if ratio <= 3.0:
        return "alta"
    return "anomalamente_alta"


@cached(ttl=60)
def liquidez_secundario(ticker: str, dias: int = 20) -> dict:
    """Volumen operado del día actual vs promedio histórico (Trading.TimeSales).

    Usa TimeSales para consistencia (en vez de MarketSnapshot) porque el
    histórico agrupado por día siempre sale de ahí. dias = ventana para el
    promedio. Clasificación: baja | media | alta | anomalamente_alta.
    """
    ticker_exacto = resolver_ticker_exacto(ticker)
    if not ticker_exacto:
        return {"error": f"no se pudo resolver ticker '{ticker}'"}

    db = get_db_trading()
    hoy = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    desde = hoy - timedelta(days=dias)

    # Agregación: volumen por día (YYYY-MM-DD) ordenado asc
    agg = list(db["TimeSales"].aggregate([
        {"$match": {
            "ticker": ticker_exacto,
            "timestamp": {"$gte": desde},
            "money": {"$gt": 0},
        }},
        {"$group": {
            "_id":   {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
            "money": {"$sum": "$money"},
        }},
        {"$sort": {"_id": 1}},
    ]))

    if not agg:
        return {
            "ticker":              ticker_exacto,
            "volumen_dia_actual":  0,
            "volumen_promedio_dia": None,
            "ratio_vs_promedio":   None,
            "dias_analizados":     0,
            "clasificacion":       "sin_datos",
        }

    hoy_str = hoy.strftime("%Y-%m-%d")
    hoy_row = next((r for r in agg if r["_id"] == hoy_str), None)
    vol_hoy = float(hoy_row["money"]) if hoy_row else 0.0

    # Para el promedio histórico excluimos el día actual
    hist = [float(r["money"]) for r in agg if r["_id"] != hoy_str]
    promedio = round(sum(hist) / len(hist), 2) if hist else None

    ratio = None
    if promedio and promedio > 0:
        ratio = round(vol_hoy / promedio, 3)

    return {
        "ticker":               ticker_exacto,
        "volumen_dia_actual":   round(vol_hoy, 2),
        "volumen_promedio_dia": promedio,
        "ratio_vs_promedio":    ratio,
        "dias_analizados":      len(hist),
        "clasificacion":        _clasificar_liquidez(ratio),
    }
