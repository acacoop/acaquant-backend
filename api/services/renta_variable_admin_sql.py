"""api/services/renta_variable_admin_sql.py — editor del catálogo de CEDEARs.

Servicio PURO (sin FastAPI). Sirve Manager → Títulos → Renta Variable
(`api/routers/manager/renta_variable.py`): el master `mercado.cedears`
(clasificación: `rubro`, `es_ia`, `ric`, `ratio`, `activo`) y el catálogo
controlado `mercado.rubros`. El alta y el descarte de un CEDEAR NO viven acá:
son `core/cedears_sql.alta` / `.descartar` (los usa el agente); acá está lo
que edita una persona desde la pantalla.

Las funciones devuelven filas afectadas o booleanos; decidir el código HTTP
(400 rubro inexistente, 404 ticker) es del router.
"""
from __future__ import annotations

from api.services._sql import _q
from core.postgres import get_pool

# Columnas editables desde la pantalla. `actualizar_cedear` rechaza cualquier
# otra: el nombre de columna se interpola en el UPDATE y NO puede venir del
# request.
COLUMNAS_EDITABLES = frozenset({"rubro", "es_ia", "ric", "ratio", "activo"})


def listar_cedears() -> list[dict]:
    """Todos los CEDEARs con su clasificación. `nombre` sale del data jsonb del master."""
    return _q(
        "SELECT ticker, ticker_corto, underlying, activo, rubro, es_ia, ric, ratio, "
        "  data->>'nombre' AS nombre "
        "FROM mercado.cedears ORDER BY ticker_corto")


def listar_rubros() -> list[dict]:
    """Catálogo controlado de rubros (para el dropdown del editor)."""
    return _q("SELECT rubro, es_ia_def FROM mercado.rubros ORDER BY rubro")


def rubro_existe(rubro: str) -> bool:
    return bool(_q("SELECT 1 FROM mercado.rubros WHERE rubro = %s", (rubro,)))


def crear_rubro(rubro: str, es_ia_def: bool = False) -> None:
    """Idempotente: si el rubro ya existe, no rompe."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mercado.rubros (rubro, es_ia_def) VALUES (%s, %s) "
            "ON CONFLICT (rubro) DO NOTHING", (rubro, es_ia_def))
        conn.commit()


def actualizar_cedear(ticker: str, sets: dict) -> int:
    """UPDATE de las columnas de `sets` para el ticker (PK). Devuelve filas
    afectadas (0 = ticker inexistente)."""
    if not sets:
        raise ValueError("sin columnas para actualizar")
    raras = set(sets) - COLUMNAS_EDITABLES
    if raras:
        raise ValueError(f"columnas no editables: {sorted(raras)}")
    cols = ", ".join(f"{k} = %({k})s" for k in sets)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE mercado.cedears SET {cols} WHERE ticker = %(ticker)s",
                    {**sets, "ticker": ticker})
        matched = cur.rowcount
        conn.commit()
    return matched


def borrar_cedear(ticker: str) -> int:
    """Borra el CEDEAR del master `mercado.cedears` y de `mercado.cedears_snapshot`.
    Devuelve filas borradas del master (0 = ticker inexistente)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mercado.cedears WHERE ticker = %s", (ticker,))
        borradas = cur.rowcount
        cur.execute("DELETE FROM mercado.cedears_snapshot WHERE ticker = %s", (ticker,))
        conn.commit()
    return borradas
