"""
backfill_breakevens.py — Script one-off.

Recorre el historial de TEM/paridad en Trading.TimeSales y construye
Trading.BreakevensHistorico con una entrada por fecha.

Requiere que main_curvas.py (o backfill_curvas.py) haya enriquecido
TimeSales con TEM (Lecaps) y paridad (CER) antes de correr esto.

Uso:
    /root/TradingAV/venv/bin/python /root/TradingAV/backfill_breakevens.py
"""

from datetime import date, datetime, timedelta
from pymongo import UpdateOne

from mongo_manager import get_mongo_client
from main_breakevens import cargar_pares, calcular_breakevens, MAX_DIFF_DIAS


def run():
    client = get_mongo_client()
    col_ts   = client["Trading"]["TimeSales"]
    col_hist = client["Trading"]["BreakevensHistorico"]

    pares         = cargar_pares(client)
    lecap_tickers = [p["lecap_ticker"] for p in pares]
    cer_tickers   = [p["cer_ticker"]   for p in pares]
    todos_tickers = list(set(lecap_tickers + cer_tickers))

    print(f"Pares encontrados: {len(pares)}")

    # Obtener todas las fechas con datos de TEM o paridad
    print("Obteniendo fechas disponibles...")
    pipeline_fechas = [
        {"$match": {
            "ticker": {"$in": todos_tickers},
            "$or": [{"TEM": {"$exists": True}}, {"paridad": {"$exists": True}}],
        }},
        {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}}}},
        {"$sort": {"_id": 1}},
    ]
    fechas = [r["_id"] for r in col_ts.aggregate(pipeline_fechas)]
    print(f"Fechas a procesar: {len(fechas)}")

    ops = []
    for fecha_str in fechas:
        fecha_ref = date.fromisoformat(fecha_str)
        inicio    = datetime.combine(fecha_ref, datetime.min.time())
        fin       = datetime.combine(fecha_ref + timedelta(days=1), datetime.min.time())

        # Última TEM por Lecap ese día
        pipeline_tem = [
            {"$match": {
                "ticker":    {"$in": lecap_tickers},
                "TEM":       {"$exists": True},
                "timestamp": {"$gte": inicio, "$lt": fin},
            }},
            {"$sort": {"timestamp": -1}},
            {"$group": {"_id": "$ticker", "TEM": {"$first": "$TEM"}}},
        ]
        tems = {r["_id"]: r["TEM"] for r in col_ts.aggregate(pipeline_tem)}

        # Última paridad por CER ese día
        pipeline_par = [
            {"$match": {
                "ticker":    {"$in": cer_tickers},
                "paridad":   {"$exists": True},
                "timestamp": {"$gte": inicio, "$lt": fin},
            }},
            {"$sort": {"timestamp": -1}},
            {"$group": {"_id": "$ticker", "paridad": {"$first": "$paridad"}}},
        ]
        paridades = {r["_id"]: r["paridad"] for r in col_ts.aggregate(pipeline_par)}

        if not tems and not paridades:
            continue

        pares_result = calcular_breakevens(pares, tems, paridades, fecha_ref)
        if not pares_result:
            continue

        doc = {
            "fecha":      fecha_str,
            "updated_at": fin,
            "pares":      pares_result,
        }
        ops.append(UpdateOne(
            {"fecha": fecha_str},
            {"$set": doc},
            upsert=True,
        ))

        if len(ops) >= 200:
            col_hist.bulk_write(ops, ordered=False)
            ops = []
            print(f"  Procesado hasta {fecha_str}...")

    if ops:
        col_hist.bulk_write(ops, ordered=False)

    print(f"\nListo. BreakevensHistorico actualizado con {len(fechas)} fechas.")


if __name__ == "__main__":
    run()
