"""scripts/drop_indices_redundantes_operaciones.py — dropea índices redundantes.

audit_db marcó el índice `concertacion` (single) de CashFlow.Operaciones como
REDUNDANTE: está cubierto por `concertacion_mercado` (es su prefijo). Las queries
que ordenan/filtran por `concertacion` usan igual el compuesto → dropear el single
libera RAM del working set del M10 sin afectar performance.

Por defecto SOLO LISTA (no toca nada). Con --apply lo dropea.

Uso:
    python -m scripts.drop_indices_redundantes_operaciones            # lista
    python -m scripts.drop_indices_redundantes_operaciones --apply    # dropea
"""
from __future__ import annotations

import sys

from pymongo.errors import OperationFailure

from core.mongo import get_mongo_client

# (colección, índice a dropear, índice que lo cubre) — confirmado con audit_db.
_REDUNDANTES = [
    ("Operaciones", "concertacion", "concertacion_mercado"),
]


def main() -> int:
    apply = "--apply" in sys.argv
    db = get_mongo_client()["CashFlow"]

    for col_name, idx, cubierto_por in _REDUNDANTES:
        col = db[col_name]
        existentes = {ix["name"] for ix in col.list_indexes()}
        print(f"\nCashFlow.{col_name} — índices: {sorted(existentes)}")
        if idx not in existentes:
            print(f"  '{idx}' no existe (ya dropeado / no estaba). OK.")
            continue
        if cubierto_por not in existentes:
            print(f"  ⚠ '{cubierto_por}' (el que cubre) NO existe → NO dropeo '{idx}' "
                  "(sería perder cobertura). Revisar.")
            continue
        if not apply:
            print(f"  [DRY] dropearía '{idx}' (cubierto por '{cubierto_por}'). "
                  "Correr con --apply.")
            continue
        try:
            col.drop_index(idx)
            print(f"  ✅ dropeado '{idx}'.")
        except OperationFailure as e:
            print(f"  ⚠ no se pudo dropear '{idx}': {e}")

    if not apply:
        print("\n(solo listado — corré con --apply para dropear)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
