"""Health check rápido de los 8 motores always-on.

Para cada colección destino de motor, mira el último timestamp escrito y
clasifica OK / LENTO / CRITICO / SIN_DATOS según un umbral por motor.

Uso:
    python -m scripts.check_motors
"""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from core.mongo import get_mongo_client

_AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")

# (motor, db, coll, campo_ts, umbral_s, tz_fallback)
#
# tz_fallback se usa solo si el doc trae el timestamp tz-naive. TimeSales
# lo escribe como ART naive (engines/valores.py: `datetime.utcnow() - 3h`);
# el resto lo escribe como UTC naive. Sin esto el script veía "hace 3 h"
# para TimeSales aunque esté escribiendo a 1s de atraso.
_CHECKS = [
    ("motor_rofex       TimeSales",          "Trading",     "TimeSales",          "timestamp",  300, _AR_TZ),
    ("motor_rofex       MarketSnapshot",     "Trading",     "MarketSnapshot",     "updated_at",  30, UTC),
    ("motor_options     OptionsSnapshot",    "Opciones",    "OptionsSnapshot",    "updated_at",  30, UTC),
    ("motor_curvas      TimeSales(duration)","Trading",     "TimeSales",          "timestamp",  300, _AR_TZ),
    ("motor_forwards    ForwardsLive",       "Trading",     "ForwardsLive",       "updated_at",  60, UTC),
    ("motor_breakevens  BreakevensLive",     "Trading",     "BreakevensLive",     "updated_at",  60, UTC),
    ("motor_caucion     CaucionSnapshot",    "Trading",     "CaucionSnapshot",    "updated_at",  30, UTC),
    ("motor_futuros_dlr FuturosDLRSnapshot", "Trading",     "FuturosDLRSnapshot", "updated_at",  30, UTC),
    ("motor_dolares     DolarSnapshot",      "Valuaciones", "DolarSnapshot",      "timestamp",   30, UTC),
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
    for nombre, db, coll, ts_field, umbral, tz_fallback in _CHECKS:
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
            ts = ts.replace(tzinfo=tz_fallback)
        delta = (now - ts.astimezone(UTC)).total_seconds()
        estado = _estado(delta, umbral)
        # Mostramos la hora en ART para que coincida con /manager/status
        hora = ts.astimezone(_AR_TZ).strftime("%H:%M:%S")
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
