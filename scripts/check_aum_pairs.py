"""check_aum_pairs.py — verifica los conteos de Valuaciones.AuM.

Imprime:
- Total documentos
- Cuentas únicas
- Fechas únicas
- Pares (fecha, cuenta) únicos = lo que el backfill --all-existing procesa
- Posiciones promedio por par

Sirve para confirmar que el script de backfill no está salteando nada
(documentos = pares × posiciones por par).

Uso:
    python -m scripts.check_aum_pairs
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from api.db import get_db_valuaciones


def main() -> int:
    db = get_db_valuaciones()
    col = db["AuM"]

    n_docs = col.count_documents({})
    n_cuentas = len(col.distinct("id_cuenta"))
    n_fechas = len(col.distinct("fecha_snapshot"))

    # Pares únicos (fecha, cuenta).
    pipe = [{"$group": {"_id": {"f": "$fecha_snapshot", "c": "$id_cuenta"}}}]
    n_pares = sum(1 for _ in col.aggregate(pipe))

    print("\n=== Valuaciones.AuM ===")
    print(f"Documentos totales        : {n_docs:>10,}")
    print(f"Cuentas únicas            : {n_cuentas:>10,}")
    print(f"Fechas únicas             : {n_fechas:>10,}")
    print(f"Pares (fecha, cuenta)     : {n_pares:>10,}   ← lo que --all-existing procesa")
    print(f"Posiciones / par (avg)    : {n_docs / n_pares:>10.2f}")
    print("")
    print("Sanity check: pares × pos_avg ≈ docs total?")
    print(f"  {n_pares:,} × {n_docs / n_pares:.2f} ≈ {n_pares * (n_docs / n_pares):,.0f}")
    print(f"  vs docs reales: {n_docs:,}")
    print("")

    # AumBackfillRuns — para chequear progreso del backfill activo.
    col_runs = db["AumBackfillRuns"]
    n_runs = col_runs.count_documents({})
    if n_runs > 0:
        print("=== Valuaciones.AumBackfillRuns ===")
        print(f"Total intentos registrados: {n_runs:>10,}")
        for st in ("ok", "empty", "failed"):
            n = col_runs.count_documents({"status": st})
            print(f"  · {st:<8}             : {n:>10,}")
        print("")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
