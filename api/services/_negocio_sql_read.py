"""Lectura de boletos desde SQL `operaciones.negocio_movimientos` devolviendo dicts
con el MISMO shape que traía `CashFlow.NegocioMovimientos` en Mongo.

Sirve para migrar a SQL los lectores de NegocioMovimientos sin tocar la matemática
de los services (pnl, valuaciones, fci_bilateral): cambia SOLO de dónde salen los
datos. Cada doc se devuelve con:
  - `fecha` como string ISO "YYYY-MM-DD" (varios callers hacen isinstance(fecha, str)).
  - numéricos (cantidad/precio/importe/mep/arancel) como float (no Decimal).

La tabla está en el search_path (operaciones,...) → se referencia sin calificar.
"""
from __future__ import annotations

from collections.abc import Iterable

from psycopg.rows import dict_row

from core.postgres import get_pool

# Campo Mongo → expresión SQL en el SELECT (alias = nombre Mongo).
_EXPR: dict[str, str] = {
    "fecha":        "to_char(fecha, 'YYYY-MM-DD') AS fecha",
    "comprobante":  "comprobante",
    "id_cuenta":    "id_cuenta",
    "cuenta":       "cuenta",
    "categoria":    "categoria",
    "op":           "op",
    "ticker":       "ticker",
    "cantidad":     "cantidad",
    "precio":       "precio",
    "importe":      "importe",
    "moneda":       "moneda",
    "mep":          "mep",
    "plazo":        "plazo",
    "lugar":        "lugar",
    "estado":       "estado",
    "informacion":  "informacion",
    "unidad":       "unidad",
    "arancel":      "arancel",
    "aranceles":    "aranceles",
}
# Campos numéricos → float (psycopg devuelve Decimal).
_NUM = {"cantidad", "precio", "importe", "mep", "arancel"}


def negocio_movimientos_rows(
    *,
    fields: Iterable[str],
    id_cuenta: str | None = None,
    cuenta_prefix: str | None = None,
    categorias: Iterable[str] | None = None,
    ticker_not_null: bool = False,
    fecha_gte: str | None = None,
    fecha_lte: str | None = None,
    fecha_prefix: str | None = None,
    comprobante_prefix: str | None = None,
    order: bool = False,
) -> list[dict]:
    """Lee operaciones.negocio_movimientos → list[dict] shape-Mongo.

    Args:
        fields: campos Mongo a traer (subset de _EXPR).
        id_cuenta: filtra `id_cuenta = X`.
        cuenta_prefix: filtra `cuenta LIKE '[X]%'` (pasar el id, sin corchetes).
        categorias: filtra `categoria = ANY(...)`.
        ticker_not_null: agrega `ticker IS NOT NULL`.
        fecha_gte / fecha_lte: rango sobre `fecha` (ISO string).
        fecha_prefix: filtra por mes `to_char(fecha,'YYYY-MM') = X`.
        comprobante_prefix: filtra `comprobante ILIKE 'X%'` (ej. 'CL', 'BOL').
        order: ordena por (fecha, comprobante) asc.
    """
    cols = list(fields)
    select = ", ".join(_EXPR[c] for c in cols)
    conds: list[str] = ["anulado_en IS NULL"]  # boletos que Aunesa anuló: nunca se leen
    p: dict = {}
    if id_cuenta is not None:
        conds.append("id_cuenta = %(idc)s")
        p["idc"] = str(id_cuenta)
    if cuenta_prefix is not None:
        conds.append("cuenta LIKE %(cpre)s")
        p["cpre"] = f"[{cuenta_prefix}]%"
    if categorias is not None:
        conds.append("categoria = ANY(%(cats)s)")
        p["cats"] = list(categorias)
    if ticker_not_null:
        conds.append("ticker IS NOT NULL")
    if fecha_gte is not None:
        conds.append("fecha >= %(fgte)s")
        p["fgte"] = fecha_gte
    if fecha_lte is not None:
        conds.append("fecha <= %(flte)s")
        p["flte"] = fecha_lte
    if fecha_prefix is not None:
        conds.append("to_char(fecha, 'YYYY-MM') = %(fpre)s")
        p["fpre"] = fecha_prefix
    if comprobante_prefix is not None:
        conds.append("comprobante ILIKE %(cmp)s")
        p["cmp"] = f"{comprobante_prefix}%"

    sql = f"SELECT {select} FROM negocio_movimientos"
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    if order:
        sql += " ORDER BY fecha, comprobante"

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, p)
        rows = cur.fetchall()
    for r in rows:
        for k in _NUM:
            if k in r and r[k] is not None:
                r[k] = float(r[k])
    return rows
