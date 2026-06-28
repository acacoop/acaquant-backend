"""Helper compartido — devuelve el MEP histórico para una fecha dada.

SQL-native (decomiso Mongo): `valuaciones.dolar` (PK timestamp, escrita por
engines/dolar_mep.py cada 15min). Para una fecha dada, devolvemos el último MEP
con `timestamp <= fin-de-día(fecha)`. Si no hay registros anteriores → None.
"""
from __future__ import annotations

from core import dolar_sql


def get_mep_for_date(fecha_iso: str) -> float | None:
    """Último MEP con `timestamp <= end-of-day(fecha_iso)`. None si no hay
    ninguno anterior (fecha muy vieja antes de empezar el feed)."""
    return dolar_sql.mep_para_fecha(fecha_iso)
