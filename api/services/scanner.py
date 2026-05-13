"""api/services/scanner.py — vista Scanner del módulo Renta Variable.

Lee `Trading.Cedears` (master categórico: sector/industria/region/país) y
`Trading.CedearsSnapshot` (live escrito por `engines/motor_cedears.py` cada
1s) y los joinea por ticker. Devuelve un row por CEDEAR activo con sus
métricas operativas (last, intraday %, vs 1D %, retorno USD descontando
CCL).

Retorno USD (`vs_1d_usd_pct`): los CEDEARs se mueven en ARS pero el
underlying es un activo USD — parte del movimiento ARS es la devaluación
implícita del CCL. Descontamos esa componente para mostrar el retorno
"real" del subyacente:

    vs_1d_usd_pct = ((1 + cedear_1d/100) / (1 + ccl_1d/100) - 1) × 100

Donde ccl_1d = (ccl_live / ccl_cierre_ayer - 1) × 100.

Patrón: una sola lectura del CCL por request (en `_ccl_pct_vs_1d`), no
por cada ticker. El cache TTL 5s del @cached evita machacar Mongo bajo
polling del frontend.
"""
from __future__ import annotations

from datetime import datetime, timezone

from api.cache import cached
from api.db import get_db_trading, get_db_valuaciones


# Si Valuaciones.DolarSnapshot._id="current" tiene timestamp más viejo que
# esto, lo consideramos stale y caemos al último Valuaciones.Dolar.
# Mismo valor que usa argy._live_dolar — coherencia entre vistas que
# muestran CCL live.
_DOLAR_SNAPSHOT_MAX_AGE_S = 60


def _ccl_pct_vs_1d() -> float | None:
    """% del CCL hoy vs cierre del día previo (en UTC).

    Lee live de Valuaciones.DolarSnapshot._id='current' (con fallback al
    último Valuaciones.Dolar si stale) y el último doc con timestamp <
    inicio de hoy UTC en Valuaciones.Dolar (= cierre día previo).

    None si falta alguno de los dos lados — el scanner muestra '--' y
    sigue funcionando, no se rompe nada.
    """
    db = get_db_valuaciones()

    # Live snapshot
    ccl_live: float | None = None
    snap = db["DolarSnapshot"].find_one(
        {"_id": "current"}, {"ccl": 1, "timestamp": 1}
    )
    if snap:
        ts = snap.get("timestamp")
        if isinstance(ts, datetime):
            age = (datetime.now(ts.tzinfo) - ts).total_seconds()
            if age <= _DOLAR_SNAPSHOT_MAX_AGE_S and snap.get("ccl") is not None:
                ccl_live = float(snap["ccl"])

    if ccl_live is None:
        # Fallback: último doc con ccl en Valuaciones.Dolar.
        latest = db["Dolar"].find_one(
            {"ccl": {"$ne": None}},
            {"_id": 0, "ccl": 1},
            sort=[("timestamp", -1)],
        )
        if latest and latest.get("ccl"):
            ccl_live = float(latest["ccl"])

    if not ccl_live or ccl_live <= 0:
        return None

    # Cierre día previo: último doc con timestamp < hoy 00:00 UTC.
    # Si ayer no hubo doc (feriado, weekend), el query devuelve el último
    # día hábil — exactamente lo que queremos para "1D".
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    prev = db["Dolar"].find_one(
        {"ccl": {"$ne": None}, "timestamp": {"$lt": today_start}},
        {"_id": 0, "ccl": 1},
        sort=[("timestamp", -1)],
    )
    if not prev or not prev.get("ccl"):
        return None
    ccl_prev = float(prev["ccl"])
    if ccl_prev <= 0:
        return None

    return (ccl_live / ccl_prev - 1) * 100


@cached(ttl=5)
def get_cedears_scanner() -> list[dict]:
    """Master + snapshot joined por ticker, con métricas operativas.

    Tickers sin snapshot todavía (motor recién arrancado, sin tick aún)
    quedan con métricas en `None` — el frontend muestra '--'.
    """
    db = get_db_trading()
    master = list(db["Cedears"].find(
        {"activo": True},
        {"_id": 0},
    ))
    snapshots = {
        s["ticker"]: s
        for s in db["CedearsSnapshot"].find({}, {"_id": 0})
    }

    # CCL una sola vez por request — no por ticker. Si es None, todas las
    # columnas USD del scanner muestran '--' (no rompe nada).
    ccl_1d = _ccl_pct_vs_1d()

    out: list[dict] = []
    for m in master:
        s = snapshots.get(m["ticker"], {})
        last  = float(s.get("last")  or 0)
        open_ = float(s.get("open")  or 0)
        close = float(s.get("close") or 0)
        high  = float(s.get("high")  or 0)
        low   = float(s.get("low")   or 0)

        intraday = ((last / open_) - 1) * 100 if last > 0 and open_ > 0 else None
        vs_1d    = ((last / close) - 1) * 100 if last > 0 and close > 0 else None

        # Retorno USD real: descuenta variación del CCL al cedear_1d.
        # Si CEDEAR sube X% y CCL sube X%, el activo en USD se mantuvo (0%).
        vs_1d_usd = None
        if vs_1d is not None and ccl_1d is not None:
            vs_1d_usd = ((1 + vs_1d / 100) / (1 + ccl_1d / 100) - 1) * 100

        out.append({
            "ticker_corto":  m["ticker_corto"],
            "underlying":    m.get("underlying"),
            "ratio_cedear":  m.get("ratio_cedear"),
            "sector":        m.get("sector"),
            "industria":     m.get("industria"),
            "region":        m.get("region"),
            "pais":          m.get("pais"),
            "last":          last if last > 0 else None,
            "open":          open_ if open_ > 0 else None,
            "high":          high if high > 0 else None,
            "low":           low if low > 0 else None,
            "close":         close if close > 0 else None,
            "intraday_pct":  intraday,
            "vs_1d_pct":     vs_1d,
            "vs_1d_usd_pct": vs_1d_usd,
            "updated_at":    s.get("updated_at"),
        })
    return out
