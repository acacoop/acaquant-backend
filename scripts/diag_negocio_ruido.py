"""diag_negocio_ruido.py — exploración de CashFlow.NegocioMovimientos para
identificar movimientos "ruido" (no arancelables / administrativos / USDL).

Read-only. Lista distinct values con conteos por campo, calidad del arancel
en cada uno, y cruces útiles para decidir qué filtrar/borrar.

Sin args corre TODO el panorama. Con --top reduce listas largas (ej. unidad
tiene cientos de tickers). Con --desde/--hasta restringe el rango de
concertación (default: todo el histórico).

Uso (desde la raíz del repo en el Droplet):
    venv/bin/python -m scripts.diag_negocio_ruido
    venv/bin/python -m scripts.diag_negocio_ruido --top 40
    venv/bin/python -m scripts.diag_negocio_ruido --desde 2026-01-01
"""
from __future__ import annotations

import argparse
from typing import Any

from core.mongo import get_mongo_client_read

_DB = "CashFlow"
_COL = "NegocioMovimientos"


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _print_header(title: str) -> None:
    print()
    print("═" * 78)
    print(f" {title}")
    print("═" * 78)


def _arancel_calidad_subdoc() -> dict[str, Any]:
    """Sub-pipeline para resumir calidad del arancel en cada grupo:
    cuántos tienen arancel > 0, cuántos en 0/null, suma.
    """
    return {
        "n_con_arancel": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$arancel", 0]}, 0]}, 1, 0]}},
        "n_sin_arancel": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$arancel", 0]}, 0]}, 0, 1]}},
        "arancel_sum":   {"$sum": {"$ifNull": ["$arancel", 0]}},
    }


def _distinct_con_conteos(col, campo: str, base_match: dict[str, Any], top: int) -> list[dict[str, Any]]:
    """Agrupa por `campo`, cuenta + arancel sum, ordena por n desc."""
    pipeline: list[dict[str, Any]] = []
    if base_match:
        pipeline.append({"$match": base_match})
    pipeline += [
        {"$group": {"_id": f"${campo}", "n": {"$sum": 1}, **_arancel_calidad_subdoc()}},
        {"$sort": {"n": -1}},
        {"$limit": top},
    ]
    return list(col.aggregate(pipeline))


def _print_distinct(titulo: str, rows: list[dict[str, Any]], total_docs: int) -> None:
    _print_header(titulo)
    print(f"{'Valor':<50} {'N':>10} {'%':>6} {'CON ARC':>9} {'SIN ARC':>9} {'SUM ARC':>14}")
    print("─" * 78)
    for r in rows:
        val = r["_id"]
        val_str = "(null)" if val is None else (str(val)[:48] if str(val) else "(empty)")
        n = int(r["n"])
        pct = (n / total_docs * 100) if total_docs else 0
        con = int(r.get("n_con_arancel") or 0)
        sin = int(r.get("n_sin_arancel") or 0)
        arc_sum = float(r.get("arancel_sum") or 0.0)
        print(f"{val_str:<50} {_fmt(n):>10} {pct:>5.1f}% {_fmt(con):>9} {_fmt(sin):>9} "
              f"{arc_sum:>14,.0f}".replace(",", "."))


def _cross_match(col, label: str, match: dict[str, Any]) -> None:
    """Para una regla candidata a "ruido" (ej. informacion='Gestión de cobranza'),
    muestra cuántos docs caen + distribución por categoría + calidad de arancel.
    """
    _print_header(f"CRUCE: {label}")
    n_total = col.count_documents(match)
    if n_total == 0:
        print("   0 docs matchean — no hay nada que mostrar.")
        return

    rows = list(col.aggregate([
        {"$match": match},
        {"$group": {"_id": "$categoria", "n": {"$sum": 1}, **_arancel_calidad_subdoc()}},
        {"$sort": {"n": -1}},
    ]))
    arancel_sum_total = sum(float(r.get("arancel_sum") or 0.0) for r in rows)
    n_con_total = sum(int(r.get("n_con_arancel") or 0) for r in rows)

    print(f"   N total: {_fmt(n_total)}   |   con arancel>0: {_fmt(n_con_total)}   "
          f"|   sum arancel: ${arancel_sum_total:,.0f}".replace(",", "."))
    print("   por categoría:")
    for r in rows[:15]:
        cat = "(null)" if r["_id"] is None else str(r["_id"])
        print(f"      {cat:<35} {_fmt(int(r['n'])):>10}  "
              f"con arc: {_fmt(int(r.get('n_con_arancel') or 0)):>8}  "
              f"sum: ${float(r.get('arancel_sum') or 0.0):,.0f}".replace(",", "."))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top", type=int, default=25,
                    help="máximo de filas por tabla distinct (default 25).")
    ap.add_argument("--desde", default=None,
                    help="restringir a fecha de concertación >= YYYY-MM-DD.")
    ap.add_argument("--hasta", default=None,
                    help="restringir a fecha de concertación <= YYYY-MM-DD.")
    args = ap.parse_args()

    col = get_mongo_client_read()[_DB][_COL]

    base_match: dict[str, Any] = {}
    if args.desde or args.hasta:
        fecha: dict[str, str] = {}
        if args.desde:
            fecha["$gte"] = args.desde
        if args.hasta:
            fecha["$lte"] = args.hasta
        base_match["fecha"] = fecha

    print(f"Colección: {_DB}.{_COL}")
    print(f"Match base: {base_match or 'TODO el histórico'}")
    n_total = col.count_documents(base_match)
    print(f"N docs en alcance: {_fmt(n_total)}")
    if n_total == 0:
        print("\nNo hay docs en ese rango. Salgo.")
        return 0

    # ── Distinct + conteos por campo ─────────────────────────────────────────
    for campo, titulo in (
        ("informacion", "DISTINCT `informacion` (lo que pide el user: 'Gestión de cobranza')"),
        ("op",          "DISTINCT `op` (charla previa: 'Interest payment', null, etc.)"),
        ("categoria",   "DISTINCT `categoria` (compra, venta, suscripcion_fci, ...)"),
        ("unidad",      "DISTINCT `unidad` (USDL = futuros DLR, resto = tickers)"),
    ):
        rows = _distinct_con_conteos(col, campo, base_match, args.top)
        _print_distinct(titulo, rows, n_total)

    # ── Cruces de las reglas candidatas a "ruido" ────────────────────────────
    _cross_match(col, "informacion == 'Gestión de cobranza'",
                 {**base_match, "informacion": "Gestión de cobranza"})

    _cross_match(col, "op == 'Interest payment'",
                 {**base_match, "op": "Interest payment"})

    _cross_match(col, "op IS NULL / vacío",
                 {**base_match, "$or": [{"op": None}, {"op": ""}, {"op": {"$exists": False}}]})

    _cross_match(col, "unidad == 'USDL' (futuros DLR)",
                 {**base_match, "unidad": "USDL"})

    # ── Combinación de TODAS las reglas candidatas (overlap matters) ─────────
    _cross_match(
        col,
        "UNIÓN de las 4 reglas (lo que limpiaría el cleanup si aprobás todas)",
        {**base_match, "$or": [
            {"informacion": "Gestión de cobranza"},
            {"op": "Interest payment"},
            {"op": None}, {"op": ""}, {"op": {"$exists": False}},
            {"unidad": "USDL"},
        ]},
    )

    print()
    print("═" * 78)
    print(" Listo. Pasame los conteos que te llaman la atención + qué reglas")
    print(" confirmás como 'ruido' y armamos la lista canónica.")
    print("═" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
