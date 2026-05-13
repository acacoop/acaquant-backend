"""Capa de servicio — opciones (chain + meta + trades históricos + update tasa).

Todas las funciones atacan la DB `Opciones` (poblada por `engines/options.py`
vía WS + `jobs/options_rollup.py` al cierre). La única mutación es
`update_opciones_tasa`, que escribe la tasa libre de riesgo en
`Opciones.Metadata.config` y limpia el cache in-process.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha1

from api.cache import cached
from api.db import get_db_opciones
from api.services.renta_fija import _ticker_filter
from core.mongo import get_mongo_client


@cached(ttl=60)
def get_opciones(instrumento: str | None = None, tipo: str | None = None) -> list:
    db = get_db_opciones()
    # Solo opciones con tick HOY. Las ilíquidas conservan updated_at de la
    # última rueda que tuvieron precio (dirty-check del engine las saltea
    # cuando bid=offer=last=0). Sin este filtro la tabla muestra strikes
    # con vol/last de días anteriores mezclados con los de hoy.
    inicio_hoy = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    filtro: dict = {"updated_at": {"$gte": inicio_hoy}}
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
    """Trades de opciones de los últimos 21 días (Opciones.Data).

    `instrumento` acepta forma corta ('GFGC10950A') o completa
    ('MERV - XMEV - GFGC10950A - 24hs') — ambas matchean.
    """
    db = get_db_opciones()
    corte = datetime.now() - timedelta(days=21)
    filtro: dict = {"timestamp": {"$gte": corte}}
    if instrumento:
        filtro["symbol"] = _ticker_filter(instrumento)
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


# ─────────────────────────────────────────────────────────────
# Costo histórico de estrategia
# ─────────────────────────────────────────────────────────────

def _leg_key(legs: list[dict]) -> str:
    """Firma estable del template para cacheo."""
    canon = [
        {"offset": int(leg.get("offset", 0)),
         "tipo": str(leg.get("tipo", "")).upper(),
         "side": str(leg.get("side", "")).lower(),
         "qty": int(leg.get("qty", 1))}
        for leg in legs
    ]
    return sha1(json.dumps(canon, sort_keys=True).encode()).hexdigest()[:12]


def _pick_px(bid: float, offer: float, last: float, side: str) -> float:
    """Replica lib/estrategias.ts::getPx.

    buy  → offer (si bid>0 y offer>0) si no last
    sell → bid   (si bid>0 y offer>0) si no last
    """
    bid = bid or 0
    offer = offer or 0
    last = last or 0
    if offer > 0 and bid > 0:
        return offer if side == "buy" else bid
    return last


@cached(ttl=60)
def _estrategia_historico_cached(
    legs_key: str,
    legs_json: str,
    bucket_min: int,
    desde_iso: str | None,
    hasta_iso: str | None,
) -> list[dict]:
    legs = json.loads(legs_json)

    client = get_mongo_client()
    col = client["Opciones"]["Data"]

    match: dict = {}
    if desde_iso or hasta_iso:
        ts: dict = {}
        if desde_iso:
            ts["$gte"] = datetime.fromisoformat(desde_iso.replace("Z", "+00:00"))
        if hasta_iso:
            ts["$lt"] = datetime.fromisoformat(hasta_iso.replace("Z", "+00:00"))
        match["timestamp"] = ts

    pipeline = [
        {"$match": match} if match else {"$match": {}},
        {"$sort": {"timestamp": 1}},
        {"$group": {
            "_id": {
                "bucket": {"$dateTrunc": {
                    "date": "$timestamp", "unit": "minute", "binSize": bucket_min,
                }},
                "symbol": "$symbol",
            },
            "bid":    {"$last": "$bid"},
            "offer":  {"$last": "$offer"},
            "last":   {"$last": "$last"},
            "strike": {"$first": "$strike"},
            "tipo":   {"$first": "$tipo"},
            "spot":   {"$last": "$spot"},
        }},
        {"$sort": {"_id.bucket": 1}},
    ]

    # Agrupo en Python por bucket → {bucket: [docs]}
    buckets: dict[datetime, list[dict]] = {}
    for r in col.aggregate(pipeline):
        b = r["_id"]["bucket"]
        d = {
            "symbol": r["_id"]["symbol"],
            "bid":    r.get("bid"),
            "offer":  r.get("offer"),
            "last":   r.get("last"),
            "strike": r.get("strike"),
            "tipo":   r.get("tipo"),
            "spot":   r.get("spot"),
        }
        buckets.setdefault(b, []).append(d)

    out: list[dict] = []
    for ts, docs in buckets.items():
        # buildPorStrike: {strike: {CALL: doc, PUT: doc}}
        por_strike: dict[float, dict] = {}
        spot = 0.0
        for d in docs:
            k = d.get("strike")
            t = d.get("tipo")
            if not k or t not in ("CALL", "PUT"):
                continue
            por_strike.setdefault(float(k), {})[t] = d
            s = d.get("spot") or 0
            if s > spot:  # mejor proxy: spot más alto reportado (tick más reciente del bucket)
                spot = float(s)

        if spot <= 0 or not por_strike:
            continue

        # liquidStrikes: hay bid>0 o offer>0 en call o put
        def _liq(d):
            return bool(d) and ((d.get("bid") or 0) > 0 or (d.get("offer") or 0) > 0)

        liquid = sorted(
            k for k, v in por_strike.items() if _liq(v.get("CALL")) or _liq(v.get("PUT"))
        )
        if not liquid:
            continue

        # ATM = strike líquido más cercano al spot
        atm_idx = min(range(len(liquid)), key=lambda i: abs(liquid[i] - spot))
        atm = liquid[atm_idx]

        # Aplicar legs (offsets)
        valid = True
        neto = 0.0
        used_k: list[float] = []
        for leg in legs:
            idx = atm_idx + int(leg.get("offset", 0))
            if idx < 0 or idx >= len(liquid):
                valid = False
                break
            K = liquid[idx]
            d = por_strike.get(K, {}).get(str(leg.get("tipo", "")).upper())
            px = _pick_px(d.get("bid"), d.get("offer"), d.get("last"), leg.get("side", "buy")) if d else 0
            if px <= 0:
                valid = False
                break
            side = str(leg.get("side", "buy")).lower()
            qty = int(leg.get("qty", 1))
            mult = 1 if side == "buy" else -1
            neto += px * qty * mult
            used_k.append(K)

        if not valid:
            continue

        uniq_k = sorted(set(used_k))
        out.append({
            "ts":      ts.replace(tzinfo=UTC).isoformat() if ts.tzinfo is None else ts.isoformat(),
            "costo":   round(neto * 100, 2),
            "atm":     atm,
            "spot":    round(spot, 2),
            "strikes": "/".join(f"{int(k)}" for k in uniq_k),
        })

    out.sort(key=lambda x: x["ts"])
    return out


def estrategia_historico(
    legs: list[dict],
    bucket_min: int = 15,
    desde: str | None = None,
    hasta: str | None = None,
) -> list[dict]:
    """Serie intradía de costo de una estrategia de opciones.

    Args:
        legs: [{offset, tipo: CALL|PUT, side: buy|sell, qty}]. El `offset`
            se aplica sobre el índice del ATM del bucket (no sobre strikes
            fijas) — replica el comportamiento live del frontend.
        bucket_min: tamaño del bucket en minutos (default 15).
        desde, hasta: ISO datetime (default: sin filtro = todo Opciones.Data).

    Returns:
        [{ts, costo, atm, spot, strikes}, ...] ordenado por ts. Buckets
        donde la estrategia no es válida (pata fuera de rango / iliquida)
        se omiten — la serie tiene huecos, no ceros.

    Pricing por pata (matchea lib/estrategias.ts):
        buy  → offer (si offer y bid >0) si no last
        sell → bid   (si offer y bid >0) si no last
    """
    if bucket_min <= 0:
        bucket_min = 15
    legs_json = json.dumps(legs, sort_keys=True)
    return _estrategia_historico_cached(
        legs_key=_leg_key(legs),
        legs_json=legs_json,
        bucket_min=int(bucket_min),
        desde_iso=desde,
        hasta_iso=hasta,
    )
