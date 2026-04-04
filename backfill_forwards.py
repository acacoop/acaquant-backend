"""
backfill_forwards.py — Script one-off.

Recorre el historial de TEAs en Trading.TimeSales y construye
Trading.ForwardsHistorico con una entrada por (fecha, curva).

Uso:
    /root/TradingAV/venv/bin/python /root/TradingAV/backfill_forwards.py
"""

from datetime import date, datetime, timedelta
from pymongo import UpdateOne
from mongo_manager import get_mongo_client


# ─── Reutilizamos la lógica de main_forwards ──────────────────────────────────

def calcular_matriz(instrumentos, tasas_tea):
    hoy_ref = None  # usamos la fecha del día, no hoy
    validos = []

    for inst in instrumentos:
        ticker = inst.get("ticker")
        tea = tasas_tea.get(ticker)
        if tea is None:
            continue
        fecha_vto_str = inst.get("fecha_vencimiento")
        if not fecha_vto_str:
            continue
        try:
            fecha_vto = date.fromisoformat(fecha_vto_str[:10])
        except Exception:
            continue
        validos.append({
            "ticker_corto": inst["ticker_corto"],
            "ticker": ticker,
            "fecha_vto": fecha_vto,
            "TEA": tea,
        })

    return validos


def calcular_forwards(validos, fecha_ref):
    for v in validos:
        dias = (v["fecha_vto"] - fecha_ref).days
        v["t"] = dias / 365.0

    validos = [v for v in validos if v["t"] > 0]
    validos.sort(key=lambda x: x["t"])

    if len(validos) < 2:
        return [], {}, {}

    ordered = [v["ticker_corto"] for v in validos]
    tasas = {v["ticker_corto"]: round(v["TEA"], 6) for v in validos}
    matrix = {}

    for i, a in enumerate(validos):
        for j, b in enumerate(validos):
            if j <= i:
                continue
            t_a, t_b = a["t"], b["t"]
            tea_a, tea_b = a["TEA"], b["TEA"]
            dt = t_b - t_a
            if dt <= 0:
                continue
            try:
                forward = ((1 + tea_b) ** t_b / (1 + tea_a) ** t_a) ** (1 / dt) - 1
                if not (-0.5 < forward < 50):
                    continue
                ticker_largo = b["ticker_corto"]
                if ticker_largo not in matrix:
                    matrix[ticker_largo] = {}
                matrix[ticker_largo][a["ticker_corto"]] = round(forward, 6)
            except Exception:
                continue

    return ordered, tasas, matrix


def run():
    client = get_mongo_client()
    col_ts = client["Trading"]["TimeSales"]
    col_hist = client["Trading"]["ForwardsHistorico"]

    # Cargar curvas
    grupos = {}
    for d in client["Trading"]["Curvas"].find({}):
        curva = d.get("curva")
        if curva:
            grupos.setdefault(curva, []).append(d)

    todos_tickers = [
        inst["ticker"]
        for insts in grupos.values()
        for inst in insts
        if inst.get("ticker")
    ]

    print(f"Curvas: {list(grupos.keys())}")
    print(f"Tickers: {len(todos_tickers)}")

    # Obtener todas las fechas disponibles en TimeSales con TEA
    print("Obteniendo fechas disponibles...")
    pipeline_fechas = [
        {"$match": {"ticker": {"$in": todos_tickers}, "TEA": {"$exists": True}}},
        {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}}}},
        {"$sort": {"_id": 1}},
    ]
    fechas = [r["_id"] for r in col_ts.aggregate(pipeline_fechas)]
    print(f"Fechas encontradas: {len(fechas)}")

    ops = []
    for fecha_str in fechas:
        fecha_ref = date.fromisoformat(fecha_str)
        inicio = datetime.combine(fecha_ref, datetime.min.time())
        fin = datetime.combine(fecha_ref + timedelta(days=1), datetime.min.time())

        # Última TEA por ticker en ese día
        pipeline_tea = [
            {"$match": {
                "ticker": {"$in": todos_tickers},
                "TEA": {"$exists": True},
                "timestamp": {"$gte": inicio, "$lt": fin}
            }},
            {"$sort": {"timestamp": -1}},
            {"$group": {"_id": "$ticker", "TEA": {"$first": "$TEA"}}},
        ]
        tasas_tea = {r["_id"]: r["TEA"] for r in col_ts.aggregate(pipeline_tea)}

        if not tasas_tea:
            continue

        for curva, instrumentos in grupos.items():
            validos = calcular_matriz(instrumentos, tasas_tea)
            ordered, tasas, matrix = calcular_forwards(validos, fecha_ref)

            if not ordered:
                continue

            doc = {
                "curva": curva,
                "fecha": fecha_str,
                "updated_at": fin,
                "tickers": ordered,
                "tasas": tasas,
                "matrix": matrix,
            }
            ops.append(UpdateOne(
                {"curva": curva, "fecha": fecha_str},
                {"$set": doc},
                upsert=True
            ))

        if len(ops) >= 200:
            col_hist.bulk_write(ops, ordered=False)
            ops = []
            print(f"  Procesado hasta {fecha_str}...")

    if ops:
        col_hist.bulk_write(ops, ordered=False)

    print(f"\nListo. ForwardsHistorico actualizado.")


if __name__ == "__main__":
    run()
