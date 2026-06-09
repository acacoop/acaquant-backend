"""Manager sub-router — Títulos → Instrumentos (solo lectura).

Vive separado de `checks.py` (admin-only) para poder gatearlo con el módulo
fino `manager_instrumentos` → así un rol comercial (asistente_comercial) puede
VER los instrumentos descubiertos de pyRofex SIN acceso a la edición de maestro
(Assets/ONs) ni a las tabs admin.

Lee Manager.PyRofexDiscovery / PyRofexInstruments (escritas por
`scripts/discovery_pyrofex.py`). Los paths se conservan (`/checks/...`) para no
tocar el frontend.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from core.mongo import get_mongo_client_read

router = APIRouter()


@router.get("/checks/discovery-pyrofex")
def discovery_pyrofex():
    """Instruments de pyRofex agrupados por CFI code (Manager.PyRofexDiscovery)."""
    client = get_mongo_client_read()
    doc = client["Manager"]["PyRofexDiscovery"].find_one({"_id": "current"}, {"_id": 0})
    if not doc:
        return {
            "ok": False,
            "message": ("Sin data en Manager.PyRofexDiscovery. "
                        "Correr en el Droplet: python -m scripts.discovery_pyrofex"),
            "total_instruments": 0, "by_cficode": [], "generated_at": None, "stale_h": None,
        }
    generated_at = doc.get("generated_at")
    stale_h: float | None = None
    if isinstance(generated_at, datetime):
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=UTC)
        stale_h = round((datetime.now(UTC) - generated_at).total_seconds() / 3600.0, 1)
    return {
        "ok": True,
        "total_instruments": doc.get("total_instruments", 0),
        "by_cficode": doc.get("by_cficode", []),
        "generated_at": generated_at.isoformat() if isinstance(generated_at, datetime) else None,
        "stale_h": stale_h,
    }


@router.get("/checks/instruments-by-cfi")
def instruments_by_cfi(cficode: str):
    """Detalle de todos los instruments de un CFI code (Manager.PyRofexInstruments)."""
    client = get_mongo_client_read()
    doc = client["Manager"]["PyRofexInstruments"].find_one({"_id": cficode})
    if not doc:
        return {
            "ok": False,
            "message": f"No hay data para CFI '{cficode}'. Correr scripts.discovery_pyrofex.",
            "cficode": cficode, "instruments": [],
        }
    return {
        "ok": True, "cficode": cficode, "count": doc.get("count", 0),
        "underlyings": doc.get("underlyings", []), "instruments": doc.get("instruments", []),
    }
