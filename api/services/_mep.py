"""Helper compartido — devuelve el MEP histórico para una fecha dada.

`Valuaciones.Dolar` se popula vía script local (script de PC oficina) y trae
docs con `timestamp` + `mep`. Para una fecha dada, devolvemos el último MEP
con `timestamp <= end-of-day(fecha)`. Si no hay docs anteriores → None.

Convención: en `Valuaciones.AuM` el campo `valuacion` está SIEMPRE en ARS
(incluso para unidades USD/USDC — el job lo pesifica al MEP del día). Para
mostrar en USD basta con dividir por el MEP de la fecha del snapshot, sin
casos especiales.
"""
from __future__ import annotations

from datetime import datetime

from api.db import get_db_valuaciones


def get_mep_for_date(fecha_iso: str) -> float | None:
    """Último MEP con `timestamp <= end-of-day(fecha_iso)`. None si no hay
    ninguno anterior (fecha muy vieja antes de empezar el feed)."""
    try:
        target = datetime.fromisoformat(fecha_iso + "T23:59:59")
    except ValueError:
        return None
    doc = get_db_valuaciones()["Dolar"].find_one(
        {"mep": {"$ne": None}, "timestamp": {"$lte": target}},
        sort=[("timestamp", -1)],
    )
    if not doc:
        return None
    try:
        return float(doc.get("mep") or 0) or None
    except (TypeError, ValueError):
        return None
