"""Capa de servicio — renta fija (MarketSnapshot + TimeSales + Curvas).

Funciones puras (sin FastAPI, sin HTTP) que consultan la data de renta fija
local: snapshot de libro/trades, histórico de trades por ticker, serie diaria
por curva, y el tool maestro `listar_curva` que enriquece la curva con TEA/
TEM/paridad/duration/convexity.

Helpers compartidos (`resolver_ticker_exacto`, `_ticker_filter`, constantes
`_CURVAS_VALIDAS`) viven acá porque la capa de renta fija es la que define
tickers y curvas; el resto de services los importa desde este módulo.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from api.cache import cached
from api.db import get_db_trading

_CURVAS_VALIDAS = ("cer", "tasa_fija", "tamar", "soberanos", "dolar_linked")
_ORDENES_VALIDOS = ("vencimiento", "volumen_dia", "tea", "duration")


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

    curva_docs = list(db["Curvas"].find(
        {"curva": curva},
        {"_id": 0, "ticker": 1, "ticker_corto": 1, "tipo": 1,
         "fecha_vencimiento": 1, "fecha_emision": 1},
    ))
    if not curva_docs:
        return []

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
