"""Health check rápido de los 8 motores always-on.

Para cada colección destino de motor, mira el último timestamp escrito y
clasifica OK / LENTO / CRITICO / SIN_DATOS según un umbral por motor.

Uso:
    python -m scripts.check_motors
"""
from datetime import UTC, datetime

from core.mongo import get_mongo_client

# (motor, db, coll, campo_ts, umbral_s)
_CHECKS = [
    ("motor_rofex       TimeSales",          "Trading",     "TimeSales",          "timestamp",  300),
    ("motor_rofex       MarketSnapshot",     "Trading",     "MarketSnapshot",     "updated_at",  30),
    ("motor_options     OptionsSnapshot",    "Opciones",    "OptionsSnapshot",    "updated_at",  30),
    ("motor_curvas      TimeSales(duration)","Trading",     "TimeSales",          "timestamp",  300),
    ("motor_forwards    ForwardsLive",       "Trading",     "ForwardsLive",       "updated_at",  60),
    ("motor_breakevens  BreakevensLive",     "Trading",     "BreakevensLive",     "updated_at",  60),
    ("motor_caucion     CaucionSnapshot",    "Trading",     "CaucionSnapshot",    "updated_at",  30),
    ("motor_futuros_dlr FuturosDLRSnapshot", "Trading",     "FuturosDLRSnapshot", "updated_at",  30),
    ("motor_dolares     DolarSnapshot",      "Valuaciones", "DolarSnapshot",      "timestamp",   30),
]

# Filtros extra por motor (e.g. motor_curvas enriquece docs con duration)
_EXTRA = {
    "motor_curvas      TimeSales(duration)": {"duration": {"$exists": True}},
}


def _estado(delta_s: float, umbral_s: int) -> str:
    if delta_s <= umbral_s:
        return "OK"
    if delta_s <= umbral_s * 3:
        return "LENTO"
    return "CRITICO"


def main() -> int:
    client = get_mongo_client()
    now = datetime.now(UTC)

    print(f"{'Motor / Colección':<42} {'Última':<10} {'Hace':>10s}   Estado")
    print("-" * 80)

    worst = "OK"
    for nombre, db, coll, ts_field, umbral in _CHECKS:
        filt = _EXTRA.get(nombre, {})
        doc = client[db][coll].find_one(
            filt, {ts_field: 1, "_id": 0}, sort=[(ts_field, -1)]
        )
        if not doc or not doc.get(ts_field):
            print(f"{nombre:<42} {'—':<10} {'—':>10s}   SIN_DATOS")
            worst = "CRITICO"
            continue
        ts = doc[ts_field]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        delta = (now - ts).total_seconds()
        estado = _estado(delta, umbral)
        hora = ts.strftime("%H:%M:%S")
        print(f"{nombre:<42} {hora:<10} {delta:>9.0f}s   {estado}")

        # Priorizar peor estado (CRITICO > LENTO > OK)
        order = {"OK": 0, "LENTO": 1, "CRITICO": 2, "SIN_DATOS": 2}
        if order[estado] > order[worst]:
            worst = estado

    print("-" * 80)
    print(f"Peor estado global: {worst}")
    return 0 if worst == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
