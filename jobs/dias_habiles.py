"""
dias_habiles.py — Carga días hábiles del calendario argentino a SQL (mercado.dias_habiles).

SQL-ONLY: ya NO escribe Mongo (Trading.DiasHabiles dado de baja). Lector único:
core.calendario.dias_habiles_ordenados.

Uso:
    python -m jobs.dias_habiles
"""

from datetime import date, timedelta

import holidays

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
    dias = generar_dias_habiles(YEAR)
    print(f"Días hábiles {YEAR}: {len(dias)}")

    rows = [{"fecha": date.fromisoformat(f)} for f in dias]
    n = write_native("mercado.dias_habiles", ["fecha"], rows)
    print(f"SQL mercado.dias_habiles: {n} upserts.")
    print("Listo.")


if __name__ == "__main__":
    run()
