"""`scripts/drop_pnl_historico.py` — BORRA `valuaciones.pnl_historico`.

La tab PNL HISTÓRICO de `/trading` se eliminó el 2026-09-04 (front y back). Esta
tabla era su único lector y su único escritor: no queda una línea de código que
la toque. Este script la borra de la base.

**Por qué es un script aparte y no salió con el mismo commit.** Sacar el
`CREATE TABLE` de `sql/schema.sql` NO borra nada: `apply_schema` solo crea. Y
adentro hay **plata tipeada a mano, día por día** — no sale de ningún motor ni
job, así que si se borra no se reconstruye desde ninguna parte. Un `DROP` que
se ejecuta solo en un deploy es exactamente la clase de cosa que se descubre
tarde.

Por eso: **sin `--si` no borra nada.** El default MUESTRA las filas (son pocas:
un renglón por día hábil desde el 1-jul-2026) para que quede la foto en la
terminal antes de decidir. Recién con `--si` hace el DROP.

Uso:
    python -m scripts.drop_pnl_historico          # muestra qué hay (NO borra)
    python -m scripts.drop_pnl_historico --si     # BORRA la tabla

Una vez corrido y confirmado, **este script se borra** (REGLA #5): ya cumplió.
"""
from __future__ import annotations

import sys

from psycopg.rows import dict_row

from core.postgres import get_job_pool

TABLA = "valuaciones.pnl_historico"


def main() -> int:
    borrar = "--si" in sys.argv

    with get_job_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT to_regclass(%s) AS t", (TABLA,))
        if not (cur.fetchone() or {}).get("t"):
            print(f"{TABLA} no existe — nada que borrar.")
            return 0

        cur.execute(f"SELECT fecha, cuenta, monto FROM {TABLA} ORDER BY cuenta, fecha")
        filas = cur.fetchall()
        print(f"\n{TABLA}: {len(filas)} fila(s)")
        if filas:
            print("\n  FECHA        CUENTA           MONTO")
            for f in filas:
                print(f"  {f['fecha']}   {str(f['cuenta'])[:14]:<14}   {f['monto']}")

        if not borrar:
            print("\n(no se borró nada — es una foto). Para borrar de verdad:")
            print("    python -m scripts.drop_pnl_historico --si\n")
            return 0

        cur.execute(f"DROP TABLE {TABLA}")
        print(f"\n✔ {TABLA} BORRADA ({len(filas)} filas). Irreversible.")
        print("  Ya podés borrar este script del repo (REGLA #5).\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
