"""
data_diashabiles.py — Carga días hábiles del calendario argentino a Trading.DiasHabiles.

Uso:
    /root/TradingAV/venv/bin/python /root/TradingAV/data_diashabiles.py
"""

from datetime import date, timedelta

import holidays
from pymongo import UpdateOne

from core.mongo import get_mongo_client
from core.pg_mirror import write_native

YEAR = 2026


def generar_dias_habiles(year):
    feriados = holidays.Argentina(years=year)
    dias = []
    d = date(year, 1, 1)
    fin = date(year, 12, 31)
    while d <= fin:
        if d.weekday() < 5 and d not in feriados:  # lunes=0 ... viernes=4
            dias.append(d.isoformat())
        d += timedelta(days=1)
    return dias


def run():
    client = get_mongo_client()
    col = client["Trading"]["DiasHabiles"]

    dias = generar_dias_habiles(YEAR)
    print(f"Días hábiles {YEAR}: {len(dias)}")

    ops = [
        UpdateOne({"fecha": f}, {"$set": {"fecha": f}}, upsert=True)
        for f in dias
    ]
    col.bulk_write(ops, ordered=False)

    # Dual-write SQL (mercado.dias_habiles) — best-effort, no levanta si PG cae.
    # Hasta que migren todos los readers de Trading.DiasHabiles → drop Mongo.
    rows = [{"fecha": date.fromisoformat(f)} for f in dias]
    n = write_native("mercado.dias_habiles", ["fecha"], rows)
    print(f"SQL mercado.dias_habiles: {n} upserts.")
    print("Listo.")


if __name__ == "__main__":
    run()
