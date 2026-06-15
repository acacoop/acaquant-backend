"""scripts/fix_valuacion_ars.py — recalcula valuacion = cantidad × precio para
TODAS las posiciones unidad='ARS' (cash en pesos) en portafolio.tenencia.

El cash en pesos NUNCA se divide por 100 (el ÷100 es solo para renta fija que
cotiza en paridad). El recálculo venía aplicando ÷100 a MONEDAS por error → el
ARS quedaba subvaluado a 1/100 (ej. cant 2.427.916 × 1 mostraba $24.3K en vez
de $2.43M). Este fix lo corrige en SQL, scopeado SOLO a unidad='ARS' (REGLA #4).

    python -m scripts.fix_valuacion_ars            # PREVIEW (no escribe): antes/después
    python -m scripts.fix_valuacion_ars --commit   # aplica el UPDATE

Idempotente: re-correrlo no cambia nada una vez aplicado.
"""
from __future__ import annotations

import sys

from core.postgres import get_pool

_WHERE = "UPPER(TRIM(unidad)) = 'ARS'"


def main() -> None:
    commit = "--commit" in sys.argv
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT count(*), "
            f"       COALESCE(sum(valuacion), 0), "
            f"       COALESCE(sum(ROUND(cantidad * precio, 2)), 0) "
            f"FROM portafolio.tenencia WHERE {_WHERE}"
        )
        n, total_antes, total_despues = cur.fetchone()
        n = n or 0
        ta, td = float(total_antes or 0), float(total_despues or 0)

        print("\n=== fix valuación ARS (cash) · portafolio.tenencia ===")
        print(f"   filas unidad='ARS' : {n}")
        print(f"   total ANTES        : {ta:>20,.2f}")
        print(f"   total DESPUÉS      : {td:>20,.2f}   (= cantidad × precio)")
        print(f"   delta              : {td - ta:>20,.2f}")

        if not commit:
            print("\n[PREVIEW] no se escribió nada. Repetí con --commit para aplicar.\n")
            return
        if not n:
            print("\n[COMMIT] no hay filas unidad='ARS' — nada que hacer.\n")
            return

        cur.execute(
            f"UPDATE portafolio.tenencia "
            f"SET valuacion = ROUND(cantidad * precio, 2) WHERE {_WHERE}"
        )
        afectadas = cur.rowcount
        conn.commit()
        print(f"\n[COMMIT] filas actualizadas: {afectadas}\n")


if __name__ == "__main__":
    main()
