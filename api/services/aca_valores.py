"""api/services/aca_valores.py — CRUD del set de cuentas "ACA VALORES" (SQL).

Backend de MANAGER → CLIENTES → ACA VALORES. Es una lista de cuentas (por `id_cuenta`)
que la mesa edita a mano; el filtro de la vista OPERACIONES la usa para ver Todas /
Solo ACA VALORES / Sin ACA VALORES.

Tabla `clientes.aca_valores` (clave = id_cuenta): {id_cuenta, denominacion,
actualizado_por/at}. Servicio puro (sin FastAPI). Mismo patrón que contrapartes_seg.
"""
from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from api.services._sql import _q
from core.postgres import get_pool


def _exec(sql: str, params: dict) -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        n = cur.rowcount
        conn.commit()
        return n


def _s(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def listar() -> dict:
    """Cuentas del set ACA VALORES (con denominación viva del JOIN a cuentas si falta)."""
    rows = _q(
        "SELECT a.id_cuenta, COALESCE(a.denominacion, c.denominacion) AS denominacion, "
        "a.actualizado_por, a.actualizado_at "
        "FROM clientes.aca_valores a LEFT JOIN clientes.cuentas c ON c.id_cuenta = a.id_cuenta "
        "ORDER BY denominacion"
    )
    return {"total": len(rows), "cuentas": rows}


def candidatos(q: str, limit: int = 30) -> dict:
    """Buscador de cuentas para AGREGAR: matchea id_cuenta o denominación (case-insensitive)
    contra clientes.cuentas, excluyendo las que ya están en el set."""
    term = f"%{(q or '').strip()}%"
    rows = _q(
        "SELECT c.id_cuenta, c.denominacion FROM clientes.cuentas c "
        "WHERE c.id_cuenta NOT IN (SELECT id_cuenta FROM clientes.aca_valores) "
        "AND (c.id_cuenta ILIKE %(t)s OR c.denominacion ILIKE %(t)s) "
        "ORDER BY c.denominacion LIMIT %(lim)s",
        {"t": term, "lim": limit},
    )
    return {"candidatos": rows}


def agregar(id_cuenta: str, actor: str = "") -> dict:
    """Suma una cuenta al set (idempotente por id_cuenta). Toma la denominación de cuentas."""
    idc = _s(id_cuenta)
    if not idc:
        raise ValueError("falta 'id_cuenta'")
    den = _q("SELECT denominacion FROM clientes.cuentas WHERE id_cuenta = %(id)s", {"id": idc})
    denom = den[0]["denominacion"] if den else None
    _exec(
        "INSERT INTO clientes.aca_valores (id_cuenta, denominacion, actualizado_por, actualizado_at) "
        "VALUES (%(id)s, %(den)s, %(por)s, %(at)s) "
        "ON CONFLICT (id_cuenta) DO UPDATE SET denominacion = EXCLUDED.denominacion, "
        "actualizado_por = EXCLUDED.actualizado_por, actualizado_at = EXCLUDED.actualizado_at",
        {"id": idc, "den": denom, "por": actor or None, "at": datetime.now(UTC)},
    )
    ids_aca_valores.cache_clear()
    return {"id_cuenta": idc, "denominacion": denom}


def quitar(id_cuenta: str) -> dict:
    idc = _s(id_cuenta)
    if not idc:
        raise ValueError("falta 'id_cuenta'")
    n = _exec("DELETE FROM clientes.aca_valores WHERE id_cuenta = %(id)s", {"id": idc})
    ids_aca_valores.cache_clear()
    return {"borrado": n}


@lru_cache(maxsize=1)
def ids_aca_valores() -> frozenset[str]:
    """IDs de cuenta del set (cacheado; se invalida al agregar/quitar). Para filtros
    que resuelven membership en Python en vez de subquery."""
    return frozenset(r["id_cuenta"] for r in _q("SELECT id_cuenta FROM clientes.aca_valores"))
