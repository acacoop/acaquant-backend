"""fix_alquiler_table.py — reconcilia portafolio.alquiler al esquema correcto.

La tabla quedó con un esquema VIEJO (keyed por `unidad`, sin id_cuenta/en_alquiler/
desde) de una versión anterior → la feature Títulos en Alquiler nunca pudo guardar
marcas (el SELECT id_cuenta fallaba). Está VACÍA, así que se dropea y se recrea con
el esquema correcto.

SEGURO (REGLA #4): aborta si la tabla tuviera filas — no borra data. Idempotente.

Corré:  python -m scripts.fix_alquiler_table
"""
from __future__ import annotations

from core.postgres import get_pool

DDL = """
CREATE TABLE portafolio.alquiler (
    id_cuenta text NOT NULL,
    unidad text NOT NULL,
    en_alquiler boolean DEFAULT false,
    cantidad numeric,
    desde date,
    hasta date,
    updated_by text,
    updated_at timestamptz,
    PRIMARY KEY (id_cuenta, unidad)
)
"""


def main() -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('portafolio.alquiler')")
        existe = cur.fetchone()[0] is not None
        n = 0
        if existe:
            cur.execute("SELECT count(*) FROM portafolio.alquiler")
            n = cur.fetchone()[0]
        if n:
            print(f"ABORTA: portafolio.alquiler tiene {n} filas — no la toco. Revisar a mano.")
            return
        cur.execute("DROP TABLE IF EXISTS portafolio.alquiler")
        cur.execute(DDL)
        conn.commit()
        print("✅ portafolio.alquiler recreada con el esquema correcto "
              "(id_cuenta, unidad, en_alquiler, cantidad, desde, hasta, PK(id_cuenta,unidad)).")
        print("   Ya podés marcar títulos en alquiler y va a persistir.")


if __name__ == "__main__":
    main()
