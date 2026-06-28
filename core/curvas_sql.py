"""core/curvas_sql.py — lectura del master de renta fija desde SQL (decomiso Mongo).

Reemplaza `Trading.Curvas` (Mongo) por `mercado.curvas` (Postgres). La columna
`data` jsonb = el doc COMPLETO (mismo shape: fechas como strings ISO, flujos
anidados) → los readers que hacían `.get('campo')` sobre el doc Mongo funcionan
sin cambios. Las columnas tipadas (`curva`, `ticker_corto`, `fecha_vencimiento`)
se usan SOLO para filtrar barato.

Capa `core/` → usable por engines (loader de motores) y por api/services.
Master chico (~57 instrumentos) → se traen los docs completos sin problema.
"""
from __future__ import annotations

from core.postgres import get_pool


def _q(where: str = "", params: tuple = ()) -> list[dict]:
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT data FROM mercado.curvas {where}", params)
            return [r[0] for r in cur.fetchall() if r[0] is not None]
    except Exception:
        return []


def cargar_todos() -> list[dict]:
    """Todos los docs del master (sin filtro). Equivale a find({})."""
    return _q()


def por_curva(curva: str) -> list[dict]:
    """Docs de una curva (tasa_fija | cer | soberanos | on_<sector> | ...)."""
    return _q("WHERE curva = %s", (curva,))


def por_curva_like(patron: str) -> list[dict]:
    """find({'curva': {'$regex': '^on'}}) → por_curva_like('on%')."""
    return _q("WHERE curva LIKE %s", (patron,))


def por_curva_not_like(patron: str) -> list[dict]:
    """find({'curva': {'$not': {'$regex': '^on'}}}) → por_curva_not_like('on%').
    Incluye los docs con curva NULL (igual que el $not de Mongo)."""
    return _q("WHERE curva NOT LIKE %s OR curva IS NULL", (patron,))


def find_one(ticker_corto: str) -> dict | None:
    """Doc por ticker_corto (PK), o None. Equivale a find_one({'ticker_corto': X})."""
    rows = _q("WHERE ticker_corto = %s", (ticker_corto,))
    return rows[0] if rows else None


def agrupado_por_curva() -> dict[str, list[dict]]:
    """Docs agrupados por `curva` (ignora sin curva). forwards/breakevens."""
    grupos: dict[str, list[dict]] = {}
    for d in cargar_todos():
        c = d.get("curva")
        if c:
            grupos.setdefault(c, []).append(d)
    return grupos


def indexado_por_ticker() -> dict[str, dict]:
    """Dict ticker → doc (ignora sin ticker). curvas enriquece TimeSales."""
    return {d["ticker"]: d for d in cargar_todos() if d.get("ticker")}
