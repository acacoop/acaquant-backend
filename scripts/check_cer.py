"""
check_cer.py — Muestra qué CER se usó para los últimos trades CER en TimeSales.
"""
import sys
sys.path.insert(0, '/root/TradingAV')

from datetime import datetime, date, timedelta
from core.mongo import get_mongo_client

client = get_mongo_client()
db = client["Trading"]

cer_dict = {d["fecha"]: float(d["valor"]) for d in db["CER"].find({}, {"fecha": 1, "valor": 1})}
dias_habiles = sorted(d["fecha"] for d in db["DiasHabiles"].find({}, {"fecha": 1, "_id": 0}))
tickers_cer = [d["ticker"] for d in db["Curvas"].find({"curva": "cer"}, {"ticker": 1})]

print(f"CER más reciente disponible: {max(cer_dict.keys())} = {cer_dict[max(cer_dict.keys())]}")
print("-" * 80)

for ticker in tickers_cer[:4]:
    docs = list(db["TimeSales"].find(
        {"ticker": ticker, "TEA": {"$exists": True}},
        sort=[("timestamp", -1)],
        limit=2
    ))
    for doc in docs:
        ts = doc.get("timestamp")
        fecha_trade = ts.date() if isinstance(ts, datetime) else None
        if not fecha_trade:
            continue

        settlement_str = next((f for f in dias_habiles if f > fecha_trade.isoformat()), None)
        if not settlement_str:
            continue

        idx = None
        for i, f in enumerate(dias_habiles):
            if f <= settlement_str:
                idx = i

        if idx is None or idx < 10:
            continue

        fecha_cer_obj = date.fromisoformat(dias_habiles[idx - 10])
        cer_val = None
        fecha_cer_usada = None
        for i in range(7):
            key = (fecha_cer_obj - timedelta(days=i)).isoformat()
            if key in cer_dict:
                cer_val = cer_dict[key]
                fecha_cer_usada = key
                break

        print(f"[{ticker}]")
        print(f"  trade={fecha_trade} | settlement={settlement_str} | CER fecha={fecha_cer_usada} | CER={cer_val}")
        print(f"  precio={doc.get('price')} | TEA={doc.get('TEA')} | paridad={doc.get('paridad')}")
        print()
