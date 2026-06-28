"""Manager sub-router — Títulos → Instrumentos (solo lectura).

Vive separado de `checks.py` (admin-only) para poder gatearlo con el módulo
fino `manager_instrumentos` → así un rol comercial (asistente_comercial) puede
VER los instrumentos descubiertos de pyRofex SIN acceso a la edición de maestro
(Assets/ONs) ni a las tabs admin.

Lee manager.pyrofex_discovery / manager.pyrofex_instruments (SQL, escritas por
`scripts/discovery_pyrofex.py`). Los paths se conservan (`/checks/...`) para no
tocar el frontend.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from core.postgres import get_pool

router = APIRouter()


@router.get("/checks/discovery-pyrofex")
def discovery_pyrofex():
    """Instruments de pyRofex agrupados por CFI code (manager.pyrofex_discovery)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT total_instruments, by_cficode, generated_at "
            "FROM manager.pyrofex_discovery WHERE id = 'current'"
        )
        row = cur.fetchone()
    if not row:
        return {
            "ok": False,
            "message": ("Sin data en manager.pyrofex_discovery. "
                        "Correr en el Droplet: python -m scripts.discovery_pyrofex"),
            "total_instruments": 0, "by_cficode": [], "generated_at": None, "stale_h": None,
        }
    total_instruments, by_cficode, generated_at = row
    stale_h: float | None = None
    if isinstance(generated_at, datetime):
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=UTC)
        stale_h = round((datetime.now(UTC) - generated_at).total_seconds() / 3600.0, 1)
    return {
        "ok": True,
        "total_instruments": total_instruments or 0,
        "by_cficode": by_cficode or [],
        "generated_at": generated_at.isoformat() if isinstance(generated_at, datetime) else None,
        "stale_h": stale_h,
    }


@router.get("/checks/instruments-by-cfi")
def instruments_by_cfi(cficode: str):
    """Detalle de todos los instruments de un CFI code (manager.pyrofex_instruments)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count, underlyings, instruments "
            "FROM manager.pyrofex_instruments WHERE cficode = %s",
            (cficode,),
        )
        row = cur.fetchone()
    if not row:
        return {
            "ok": False,
            "message": f"No hay data para CFI '{cficode}'. Correr scripts.discovery_pyrofex.",
            "cficode": cficode, "instruments": [],
        }
    count, underlyings, instruments = row
    return {
        "ok": True, "cficode": cficode, "count": count or 0,
        "underlyings": underlyings or [], "instruments": instruments or [],
    }
