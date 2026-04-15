"""
check_forwards.py — Diagnóstico de forwards.

Muestra por curva:
  - Todos los instrumentos en Trading.Curvas
  - Cuáles tienen TEA disponible en TimeSales (aparecerían en forwards)
  - Cuáles NO tienen TEA (están ausentes en la matriz)
  - Comparación: orden actual en ForwardsLive vs orden por fecha_vencimiento
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.mongo import get_mongo_client


def main():
    client = get_mongo_client()
    db = client["Trading"]

    # 1. Cargar todos los instrumentos de Curvas agrupados por curva
    curvas_docs = list(db["Curvas"].find({}))
    grupos = {}
    for d in curvas_docs:
        curva = d.get("curva", "?")
        grupos.setdefault(curva, []).append(d)

    print(f"Curvas en Trading.Curvas: {sorted(grupos.keys())}\n")
    print("=" * 70)

    # 2. Para cada curva, ver qué tiene TEA en TimeSales
    for curva, instrumentos in sorted(grupos.items()):
        tickers = [i["ticker"] for i in instrumentos if i.get("ticker")]

        # Última TEA por ticker
        pipeline = [
            {"$match": {"ticker": {"$in": tickers}, "TEA": {"$exists": True}, "duration": {"$exists": True}}},
            {"$sort": {"timestamp": -1}},
            {"$group": {
                "_id":      "$ticker",
                "TEA":      {"$first": "$TEA"},
                "duration": {"$first": "$duration"},
                "ts":       {"$first": "$timestamp"},
            }},
        ]
        teas = {r["_id"]: r for r in db["TimeSales"].aggregate(pipeline)}

        print(f"\nCURVA: {curva}  ({len(instrumentos)} instrumentos en Curvas)")
        print(f"{'ticker_corto':<12} {'ticker':<20} {'vencimiento':<14} {'TEA':>8} {'duration':>10} {'último trade'}")
        print("-" * 80)

        # Ordenar por fecha_vencimiento para mostrar el orden "esperado"
        instrumentos_sorted = sorted(instrumentos, key=lambda x: x.get("fecha_vencimiento", "9999"))

        con_tea = 0
        sin_tea = 0
        for inst in instrumentos_sorted:
            ticker  = inst.get("ticker", "?")
            tc      = inst.get("ticker_corto", "?")
            venc    = inst.get("fecha_vencimiento", "?")
            datos   = teas.get(ticker)
            if datos:
                tea_str  = f"{datos['TEA']:.2%}"
                dur_str  = f"{datos['duration']:.3f}"
                ts_str   = datos['ts'].strftime("%d/%m %H:%M") if datos.get('ts') else "-"
                con_tea += 1
            else:
                tea_str  = "SIN TEA"
                dur_str  = "-"
                ts_str   = "-"
                sin_tea += 1
            marca = "  " if datos else "❌"
            print(f"{marca} {tc:<12} {ticker:<20} {venc!s:<14} {tea_str:>8} {dur_str:>10}   {ts_str}")

        print(f"\n  ✅ Con TEA: {con_tea}  |  ❌ Sin TEA: {sin_tea}")

        # 3. Comparar con ForwardsLive
        live = db["ForwardsLive"].find_one({"curva": curva})
        if live:
            order_live = live.get("tickers", [])
            order_venc = [inst.get("ticker_corto") for inst in instrumentos_sorted if teas.get(inst.get("ticker"))]
            match = order_live == order_venc
            print(f"\n  Orden en ForwardsLive:      {order_live}")
            print(f"  Orden por fecha_vencimiento: {order_venc}")
            print(f"  {'✅ Coinciden' if match else '⚠️  DIFIEREN'}")
        else:
            print("\n  ⚠️  Sin doc en ForwardsLive para esta curva")

        print("=" * 70)

    client.close()


if __name__ == "__main__":
    main()
