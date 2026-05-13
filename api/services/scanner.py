"""api/services/scanner.py — vista Scanner del módulo Renta Variable.

Lee `Trading.Cedears` (master categórico: sector/industria/region/país) y
`Trading.CedearsSnapshot` (live escrito por `engines/motor_cedears.py` cada
1s) y los joinea por ticker. Devuelve un row por CEDEAR activo con sus
métricas operativas básicas (last, intraday %, vs 1D %).

El motor escribe el snapshot completo (OHLC + last + updated_at). Acá
calculamos las métricas derivadas que sirven al frontend para evitar que
cada cliente las recompute.
"""
from __future__ import annotations

from api.cache import cached
from api.db import get_db_trading


@cached(ttl=5)
def get_cedears_scanner() -> list[dict]:
    """Master + snapshot joined por ticker.

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

        out.append({
            "ticker_corto": m["ticker_corto"],
            "underlying":   m.get("underlying"),
            "ratio_cedear": m.get("ratio_cedear"),
            "sector":       m.get("sector"),
            "industria":    m.get("industria"),
            "region":       m.get("region"),
            "pais":         m.get("pais"),
            "last":         last if last > 0 else None,
            "open":         open_ if open_ > 0 else None,
            "high":         high if high > 0 else None,
            "low":          low if low > 0 else None,
            "close":        close if close > 0 else None,
            "intraday_pct": intraday,
            "vs_1d_pct":    vs_1d,
            "updated_at":   s.get("updated_at"),
        })
    return out
