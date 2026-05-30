"""diag_categoria_otro.py — distinct `informacion` con categoria='otro' o null.

Identifica los patrones de Aunesa que NO está clasificando `categorizar()` en
api/services/aunesa_negocio.py. Cada fila es candidata a sumarse como regla
en esa función para que deje de caer al fallback.

Read-only. Lista por conteo descendente para priorizar — primero los que más
se repiten (más impacto al sumar la regla).

Uso:
    venv/bin/python -m scripts.diag_categoria_otro
    venv/bin/python -m scripts.diag_categoria_otro --top 50
    venv/bin/python -m scripts.diag_categoria_otro --desde 2026-01-01
"""
from __future__ import annotations

import argparse
from typing import Any

from core.mongo import get_mongo_client_read

_DB = "CashFlow"
_COL = "NegocioMovimientos"


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top", type=int, default=30,
                    help="filas a mostrar (default 30).")
    ap.add_argument("--desde", default=None,
                    help="restringir a fecha >= YYYY-MM-DD.")
    ap.add_argument("--hasta", default=None,
                    help="restringir a fecha <= YYYY-MM-DD.")
    args = ap.parse_args()

    col = get_mongo_client_read()[_DB][_COL]

    match: dict[str, Any] = {
        "$or": [{"categoria": "otro"}, {"categoria": None},
                {"categoria": {"$exists": False}}],
    }
    if args.desde or args.hasta:
        fecha: dict[str, str] = {}
        if args.desde:
            fecha["$gte"] = args.desde
        if args.hasta:
            fecha["$lte"] = args.hasta
        match["fecha"] = fecha

    n_total = col.count_documents(match)
    print(f"Docs con categoria='otro' o null: {_fmt(n_total)}")
    if n_total == 0:
        print("Nada que mostrar.")
        return 0

    # Distinct informacion con conteos + ejemplo de op y arancel
    rows = list(col.aggregate([
        {"$match": match},
        {"$group": {
            "_id":         "$informacion",
            "n":           {"$sum": 1},
            "op_ejemplo":  {"$first": "$op"},
            "n_con_arc":   {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$arancel", 0]}, 0]}, 1, 0]}},
            "arc_sum":     {"$sum": {"$ifNull": ["$arancel", 0]}},
        }},
        {"$sort": {"n": -1}},
        {"$limit": args.top},
    ]))

    print()
    print(f"{'N':>10}  {'CON ARC':>8}  {'SUM ARC':>14}  OP EJEMPLO          INFORMACIÓN")
    print("─" * 110)
    for r in rows:
        info = r["_id"] or "(null)"
        info_str = str(info)[:60]
        op = (r.get("op_ejemplo") or "")[:18]
        arc_sum = float(r.get("arc_sum") or 0)
        print(f"{_fmt(int(r['n'])):>10}  {_fmt(int(r.get('n_con_arc') or 0)):>8}  "
              f"{arc_sum:>14,.0f}  {op:<18}  {info_str}".replace(",", "."))

    print()
    print("Las filas con CON ARC > 0 son señal fuerte: son boletos arancelables")
    print("que se están perdiendo del control comercial por no clasificar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
