"""api/services/pyrofex_discovery_sql.py — instrumentos descubiertos de pyRofex.

Servicio PURO (sin FastAPI). Lee `manager.pyrofex_discovery` (resumen por CFI
code, una sola fila `id = 'current'`) y `manager.pyrofex_instruments` (detalle
por CFI). Las dos las escribe `scripts/discovery_pyrofex.py`; acá solo se
leen. Sirve `/api/manager/checks/{discovery-pyrofex,instruments-by-cfi}`
(`api/routers/manager/instrumentos.py`).
"""
from __future__ import annotations

from datetime import UTC, datetime

from api.services._sql import _q


def discovery_pyrofex() -> dict:
    """Resumen del discovery: total de instruments, agrupado por CFI code y
    hace cuántas horas se generó (`stale_h`). `ok=False` si nunca se corrió."""
    rows = _q("SELECT total_instruments, by_cficode, generated_at "
              "FROM manager.pyrofex_discovery WHERE id = 'current'")
    if not rows:
        return {
            "ok": False,
            "message": ("Sin data en manager.pyrofex_discovery. "
                        "Correr en el Droplet: python -m scripts.discovery_pyrofex"),
            "total_instruments": 0, "by_cficode": [], "generated_at": None, "stale_h": None,
        }
    r = rows[0]
    generated_at = r["generated_at"]
    stale_h: float | None = None
    if isinstance(generated_at, datetime):
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=UTC)
        stale_h = round((datetime.now(UTC) - generated_at).total_seconds() / 3600.0, 1)
    return {
        "ok": True,
        "total_instruments": r["total_instruments"] or 0,
        "by_cficode": r["by_cficode"] or [],
        "generated_at": generated_at.isoformat() if isinstance(generated_at, datetime) else None,
        "stale_h": stale_h,
    }


def instruments_by_cfi(cficode: str) -> dict:
    """Detalle de todos los instruments de un CFI code."""
    rows = _q("SELECT count, underlyings, instruments "
              "FROM manager.pyrofex_instruments WHERE cficode = %s", (cficode,))
    if not rows:
        return {
            "ok": False,
            "message": f"No hay data para CFI '{cficode}'. Correr scripts.discovery_pyrofex.",
            "cficode": cficode, "instruments": [],
        }
    r = rows[0]
    return {
        "ok": True, "cficode": cficode, "count": r["count"] or 0,
        "underlyings": r["underlyings"] or [], "instruments": r["instruments"] or [],
    }
