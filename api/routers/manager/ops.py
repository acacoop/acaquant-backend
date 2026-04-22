"""GET /api/manager/changelog + GET /api/manager/latencia — utilidades operativas."""
from __future__ import annotations

import time as _time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query

from api.routers.manager._common import _AR_TZ
from core.mongo import get_mongo_client_read

router = APIRouter()


@router.get("/changelog")
def get_changelog(limit: int = Query(100, le=500)):
    docs = list(
        get_mongo_client_read()["Manager"]["ChangeLog"]
        .find({}, {"_id": 0})
        .sort("when", -1)
        .limit(limit)
    )
    for d in docs:
        if isinstance(d.get("when"), datetime):
            d["when"] = d["when"].astimezone(_AR_TZ).strftime("%Y-%m-%d %H:%M:%S")
    return docs


@router.get("/latencia")
def benchmark_latencia():
    client = get_mongo_client_read()
    resultados = []

    def medir(vista: str, db_name: str, coll: str, query: dict | None = None, descripcion: str = ""):
        t0 = _time.perf_counter()
        docs = list(client[db_name][coll].find(query or {}, {"_id": 0}))
        ms = (_time.perf_counter() - t0) * 1000
        resultados.append({
            "vista": vista, "coleccion": coll, "descripcion": descripcion,
            "docs": len(docs), "ms": round(ms, 1),
            "ms_doc": round(ms / max(len(docs), 1), 3),
        })

    desde_24h = datetime.now(UTC) - timedelta(hours=24)

    medir("Mercado",          "Trading",     "MarketSnapshot",   descripcion="Snapshot tickers en tiempo real")
    medir("Mercado",          "Trading",     "TimeSales",        {"timestamp": {"$gte": desde_24h}}, "Trades últimas 24h")
    medir("Forwards",         "Trading",     "ForwardsLive",     descripcion="Matriz forward live")
    medir("Breakevens",       "Trading",     "BreakevensLive",   descripcion="Breakevens live")
    medir("Forwards hist.",   "Trading",     "ForwardsHistorico",descripcion="Historial forwards")
    medir("Breakevens hist.", "Trading",     "BreakevensHistorico", descripcion="Historial breakevens")
    medir("BCRA",             "Trading",     "CER",              descripcion="Serie CER histórica")
    medir("BCRA",             "Trading",     "DOLAR",            descripcion="Dólar A3500")
    medir("Curvas",           "Trading",     "Curvas",           descripcion="Definición instrumentos renta fija")
    medir("Opciones",         "Opciones",    "OptionsSnapshot",  descripcion="Snapshot opciones GGAL")
    medir("AuM (full)",       "Valuaciones", "AuM",              descripcion="Todos los snapshots históricos")
    medir("Carteras",         "Valuaciones", "Carteras",         descripcion="Posiciones mes actual")
    medir("Assets",           "Valuaciones", "Assets",           descripcion="Metadata instrumentos")
    medir("CashFlow Mov.",    "CashFlow",    "Movimientos",      descripcion="Historial movimientos dinero")
    medir("Flujo CP",         "CashFlow",    "Flujo",            descripcion="Operaciones por contraparte")

    resultados.sort(key=lambda r: -r["ms"])
    return {
        "total_ms":   round(sum(r["ms"] for r in resultados), 1),
        "total_docs": sum(r["docs"] for r in resultados),
        "queries":    len(resultados),
        "resultados": resultados,
    }
