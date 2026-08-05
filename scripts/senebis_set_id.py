"""Fija el PRÓXIMO ID de operaciones.senebis (la secuencia global del export).

El ID del Excel de SENEBIS es la identity de la tabla: una secuencia GLOBAL
que nunca se resetea. El schema la crea arrancando en 13630 (continuando la
numeración de la planilla vieja, que iba por 13629 el 2026-08-05). Si hay que
arrancar en otro número — o corregirlo antes de empezar a cargar — se corre:

    python -m scripts.senebis_set_id --next 14000   # el próximo INSERT sale 14000
    python -m scripts.senebis_set_id                # solo muestra el estado actual

Seguro: solo toca la secuencia (no borra ni edita filas) y se niega a fijar un
número ≤ al máximo ID ya cargado (rompería inserts futuros con PK duplicada).
One-shot — borrar cuando la numeración quede consolidada (REGLA #5).
"""
from __future__ import annotations

import argparse

from core.postgres import get_pool


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--next", dest="next_id", type=int, default=None,
                    help="próximo ID a asignar (sin este flag solo muestra estado)")
    args = ap.parse_args()

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(id), 0), COUNT(*) FROM operaciones.senebis")
        max_id, n = cur.fetchone()
        print(f"operaciones.senebis: {n} filas, máximo ID cargado = {max_id or '—'}")

        if args.next_id is None:
            return
        if args.next_id <= max_id:
            raise SystemExit(
                f"ABORT: --next {args.next_id} es ≤ al máximo ID cargado ({max_id}); "
                "los próximos inserts chocarían por PK duplicada. Elegí un número mayor.")
        cur.execute(
            f"ALTER TABLE operaciones.senebis ALTER COLUMN id RESTART WITH {int(args.next_id)}")
        conn.commit()
        print(f"OK: el próximo ID de SENEBIS será {args.next_id}")


if __name__ == "__main__":
    main()
