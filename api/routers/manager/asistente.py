"""GET /api/manager/asistente/* — observabilidad del asistente IA.

Métricas, logs, timeseries y ranking de tools invocadas. Lee
Manager.AsistenteLogs (escrita por POST /api/chat).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query

from core.mongo import get_mongo_client_read

router = APIRouter()

# Precios Gemini 2.5 Flash. USD por token. Se mantienen para paridad de
# costo con el provider previo; el cálculo sirve de referencia aunque el
# provider activo sea Claude.
_GEMINI_FLASH_PRICE_IN  = 0.30 / 1_000_000
_GEMINI_FLASH_PRICE_OUT = 2.50 / 1_000_000


def _asistente_coll():
    return get_mongo_client_read()["Manager"]["AsistenteLogs"]


@router.get("/asistente/stats")
def asistente_stats(horas: int = Query(24, ge=1, le=720)):
    """Métricas agregadas del asistente en las últimas `horas` horas."""
    desde = datetime.now(UTC) - timedelta(hours=horas)
    docs = list(_asistente_coll().find({"ts": {"$gte": desde}}, {"_id": 0}))

    total = len(docs)
    ok        = sum(1 for d in docs if d.get("estado") == "ok")
    errores   = sum(1 for d in docs if d.get("estado") == "error")
    truncated = sum(1 for d in docs if d.get("estado") == "truncated")

    usages = [d.get("usage") or {} for d in docs if d.get("usage")]
    tokens_in  = sum(u.get("promptTokenCount", 0) or 0 for u in usages)
    tokens_out = sum(u.get("candidatesTokenCount", 0) or 0 for u in usages)

    elapsed = [d.get("elapsed_s") for d in docs if isinstance(d.get("elapsed_s"), (int, float))]
    elapsed.sort()
    avg_s = round(sum(elapsed) / len(elapsed), 2) if elapsed else 0.0
    p95_s = round(elapsed[int(len(elapsed) * 0.95)], 2) if len(elapsed) >= 20 else (
        round(max(elapsed), 2) if elapsed else 0.0
    )

    costo_usd = round(
        tokens_in * _GEMINI_FLASH_PRICE_IN + tokens_out * _GEMINI_FLASH_PRICE_OUT,
        4,
    )

    return {
        "periodo_horas": horas,
        "conversaciones_total": total,
        "conversaciones_ok": ok,
        "conversaciones_error": errores,
        "conversaciones_truncated": truncated,
        "pct_error": round(errores / total * 100, 2) if total else 0,
        "pct_truncated": round(truncated / total * 100, 2) if total else 0,
        "tokens_input": tokens_in,
        "tokens_output": tokens_out,
        "tokens_total": tokens_in + tokens_out,
        "latencia_avg_s": avg_s,
        "latencia_p95_s": p95_s,
        "costo_estimado_usd": costo_usd,
    }


@router.get("/asistente/logs")
def asistente_logs(
    limit: int = Query(50, ge=1, le=500),
    estado: str = Query("all", description="all | ok | error | truncated"),
    horas: int = Query(24, ge=1, le=720),
):
    """Últimas N conversaciones, ordenadas desc por timestamp."""
    desde = datetime.now(UTC) - timedelta(hours=horas)
    filtro: dict = {"ts": {"$gte": desde}}
    if estado in ("ok", "error", "truncated"):
        filtro["estado"] = estado

    cur = _asistente_coll().find(filtro, {"_id": 0}).sort("ts", -1).limit(limit)
    docs = []
    for d in cur:
        # Serialización: pymongo devuelve datetime naive (BSON siempre es UTC
        # internamente). Le anotamos tzinfo=UTC para que el ISO output lleve
        # +00:00 y el browser no lo interprete como local.
        v = d.get("ts")
        if isinstance(v, datetime):
            d["ts"] = (v if v.tzinfo else v.replace(tzinfo=UTC)).isoformat()
        docs.append(d)
    return docs


@router.get("/asistente/timeseries")
def asistente_timeseries(horas: int = Query(24, ge=1, le=720)):
    """Serie por hora: conversaciones, tokens, errores."""
    desde = datetime.now(UTC) - timedelta(hours=horas)
    pipeline = [
        {"$match": {"ts": {"$gte": desde}}},
        {"$group": {
            "_id": {
                "y": {"$year": "$ts"},
                "m": {"$month": "$ts"},
                "d": {"$dayOfMonth": "$ts"},
                "h": {"$hour": "$ts"},
            },
            "count": {"$sum": 1},
            "tokens": {"$sum": {"$ifNull": ["$usage.totalTokenCount", 0]}},
            "errors": {"$sum": {"$cond": [{"$eq": ["$estado", "error"]}, 1, 0]}},
        }},
        {"$sort": {"_id": 1}},
    ]
    rows = list(_asistente_coll().aggregate(pipeline))
    return [
        {
            "bucket": datetime(r["_id"]["y"], r["_id"]["m"], r["_id"]["d"], r["_id"]["h"], tzinfo=UTC).isoformat(),
            "count": r["count"],
            "tokens": r["tokens"] or 0,
            "errors": r["errors"],
        }
        for r in rows
    ]


@router.get("/asistente/tools-ranking")
def asistente_tools_ranking(horas: int = Query(24, ge=1, le=720)):
    """Ranking de tools invocadas + tasa de éxito/fallo."""
    desde = datetime.now(UTC) - timedelta(hours=horas)
    pipeline = [
        {"$match": {"ts": {"$gte": desde}, "tool_calls": {"$exists": True, "$ne": []}}},
        {"$unwind": "$tool_calls"},
        {"$group": {
            "_id": "$tool_calls.name",
            "calls": {"$sum": 1},
            "ok":    {"$sum": {"$cond": [{"$eq": ["$tool_calls.ok", True]}, 1, 0]}},
            "fail":  {"$sum": {"$cond": [{"$eq": ["$tool_calls.ok", False]}, 1, 0]}},
        }},
        {"$sort": {"calls": -1}},
    ]
    rows = list(_asistente_coll().aggregate(pipeline))
    return [
        {"tool": r["_id"], "calls": r["calls"], "ok": r["ok"], "fail": r["fail"]}
        for r in rows
    ]
