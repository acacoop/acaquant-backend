"""check_cuenta_latest.py — diagnóstico: ver el snapshot AuM más
reciente para una cuenta dada.

Sirve para chequear si Valuaciones.AuM tiene las posiciones de cash
(ARS/USD) en el último fecha_snapshot de una cuenta. Si las filas
ARS/USD no aparecen acá, la data en Mongo está incompleta para esa
fecha — no es problema de frontend / cache.

Uso:
    python -m scripts.check_cuenta_latest 805
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from api.db import get_db_valuaciones


def _fmt(v) -> str:
    """Format number con 2 decimales, alineado, o '—' si None."""
    if v is None:
        return "—"
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("id_cuenta", help="ID numérico de la cuenta, ej '805'")
    args = parser.parse_args()

    id_cuenta = args.id_cuenta
    db = get_db_valuaciones()

    # 1. Última fecha_snapshot para esta cuenta.
    latest = list(
        db["AuM"]
        .find({"id_cuenta": id_cuenta}, {"_id": 0, "fecha_snapshot": 1})
        .sort("fecha_snapshot", -1)
        .limit(1)
    )

    print(f"\n=== Cuenta {id_cuenta} ===")
    if not latest:
        print(f"[!!] No hay docs en Valuaciones.AuM para id_cuenta='{id_cuenta}'")
        return 1

    fecha = latest[0]["fecha_snapshot"]

    # Cuántas fechas únicas tiene esta cuenta — útil para context.
    n_fechas = len(db["AuM"].distinct("fecha_snapshot", {"id_cuenta": id_cuenta}))
    print(f"Latest fecha_snapshot : {fecha}")
    print(f"Fechas con data       : {n_fechas}")

    # 2. Todas las filas para ese fecha_snapshot.
    rows = list(
        db["AuM"]
        .find(
            {"id_cuenta": id_cuenta, "fecha_snapshot": fecha},
            {"_id": 0, "unidad": 1, "tipoTitulo": 1,
             "cantidad": 1, "precio": 1, "valuacion": 1},
        )
        .sort("valuacion", -1)
    )
    print(f"Total filas en fecha  : {len(rows)}\n")

    # Separar cash vs el resto para destacar.
    cash_rows = [r for r in rows if str(r.get("unidad") or "") in ("ARS", "USD")]
    other_rows = [r for r in rows if str(r.get("unidad") or "") not in ("ARS", "USD")]

    header = f"{'unidad':<12} | {'tipoTitulo':<28} | {'cantidad':>16} | {'precio':>12} | {'valuacion':>16}"
    sep = "-" * len(header)

    if cash_rows:
        print("[ ✓ CASH POSITIONS — ARS/USD ]")
        print(header)
        print(sep)
        for r in cash_rows:
            unidad = str(r.get("unidad") or "")
            tipo = str(r.get("tipoTitulo") or "")[:28]
            print(f"{unidad:<12} | {tipo:<28} | {_fmt(r.get('cantidad')):>16} | {_fmt(r.get('precio')):>12} | {_fmt(r.get('valuacion')):>16}")
        print()
    else:
        print("[ !! NO HAY FILAS ARS/USD EN ESTA FECHA ]")
        print("    → la data Mongo de esta cuenta está incompleta para el último snapshot.")
        print("    → posible causa: el doc fue escrito antes de remover el filtro cash_neg.")
        print()

    if other_rows:
        print(f"[ POSITIONS · {len(other_rows)} filas ]")
        print(header)
        print(sep)
        for r in other_rows:
            unidad = str(r.get("unidad") or "")
            tipo = str(r.get("tipoTitulo") or "")[:28]
            print(f"{unidad:<12} | {tipo:<28} | {_fmt(r.get('cantidad')):>16} | {_fmt(r.get('precio')):>12} | {_fmt(r.get('valuacion')):>16}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
