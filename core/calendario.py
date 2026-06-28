"""Días hábiles del calendario argentino — SQL-ONLY (mercado.dias_habiles).

Fuente ÚNICA: `mercado.dias_habiles` (poblada por jobs.dias_habiles, SQL-native).
SIN fallback a Mongo — `Trading.DiasHabiles` está DADO DE BAJA. Si la tabla SQL
no está poblada, correr `python -m jobs.dias_habiles` (no hay red Mongo a propósito).

Único lector de días hábiles del repo (engines + api + jobs lo usan).
"""
from __future__ import annotations

from core.postgres import get_pool


def dias_habiles_ordenados(_mongo_client=None) -> list[str]:
    """Lista ASC de días hábiles 'YYYY-MM-DD' desde mercado.dias_habiles (SQL-only).
    `_mongo_client` se ignora — está solo por compat de firma con los call-sites
    de engines que antes pasaban el cliente Mongo."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT to_char(fecha, 'YYYY-MM-DD') FROM mercado.dias_habiles ORDER BY fecha"
        )
        return [r[0] for r in cur.fetchall()]
