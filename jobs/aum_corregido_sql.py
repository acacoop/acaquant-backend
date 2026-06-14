"""jobs/aum_corregido_sql.py — construye la tabla SQL `aum_corregido`.

La vista /aum ya tiene dual-run (carteras.py → portfolio_sql.py con ?_engine/
PORTFOLIO_SQL). Pero portfolio_sql.py lee `FROM aum`, que es el espejo con las
FECHAS MAL. Esta tabla es el reemplazo CORREGIDO: misma forma que `aum` pero
construida desde `portafolio.tenencia` (fechas reales, regla H1).

Aplica las MISMAS exclusiones que Valuaciones.AuM (`jobs._aum_filters.is_excluded`)
para que los totales sean fieles — `portafolio.tenencia` es SIN exclusiones. La 255
NO se excluye acá (igual que el espejo `aum`): la saca portfolio_sql.py en la query.
`tipo_titulo` (que el SQL de tenencia no tiene) se completa desde el espejo `aum`
(unidad → tipo más reciente).

Idempotente: swap dentro de una transacción (TRUNCATE + INSERT). Re-correrlo
reconstruye. Pensado para correr fuera de rueda (lee/escribe Postgres, sin Mongo
salvo las listas de contrapartes para is_excluded).

Uso:
    python -m jobs.aum_corregido_sql            # construye/reemplaza aum_corregido
    python -m jobs.aum_corregido_sql --dry-run  # cuenta filas, no escribe
"""
from __future__ import annotations

import sys

from core.postgres import get_pool
from jobs._aum_filters import (
    is_excluded,
    load_contrapartes_id_cuentas,
    load_contrapartes_names,
)

_DDL = """
CREATE TABLE IF NOT EXISTS aum_corregido (
    fecha_snapshot date    NOT NULL,
    id_cuenta      text    NOT NULL,
    unidad         text    NOT NULL,
    cantidad       numeric,
    cuenta         text,
    precio         numeric,
    valuacion      numeric,
    tipo_titulo    text
)
"""
_IX = [
    "CREATE INDEX IF NOT EXISTS ix_aumcorr_cuenta ON aum_corregido(id_cuenta, fecha_snapshot)",
    "CREATE INDEX IF NOT EXISTS ix_aumcorr_unidad ON aum_corregido(unidad, fecha_snapshot)",
    "CREATE INDEX IF NOT EXISTS ix_aumcorr_fecha  ON aum_corregido(fecha_snapshot)",
]


def _tipo_por_unidad(cur) -> dict[str, str]:
    """unidad → tipo_titulo más reciente, del espejo `aum` (mismo origen Mongo)."""
    cur.execute(
        "SELECT DISTINCT ON (unidad) unidad, tipo_titulo FROM aum "
        "WHERE tipo_titulo IS NOT NULL ORDER BY unidad, fecha_snapshot DESC")
    return {u: t for u, t in cur.fetchall() if u}


def main() -> int:
    dry = "--dry-run" in sys.argv
    cont_ids = load_contrapartes_id_cuentas()
    cont_names = load_contrapartes_names()

    with get_pool().connection() as conn, conn.cursor() as cur:
        tipo_map = _tipo_por_unidad(cur)
        cur.execute(
            "SELECT fecha, id_cuenta, cuenta, unidad, cantidad, precio, valuacion "
            "FROM portafolio.tenencia")
        filas_src = cur.fetchall()

        total = len(filas_src)
        rows: list[tuple] = []
        excluidas = 0
        for fecha, idc, cuenta, unidad, cant, prec, val in filas_src:
            if is_excluded(cuenta, unidad, id_cuenta=str(idc),
                           contrapartes_ids=cont_ids, contrapartes_names=cont_names):
                excluidas += 1
                continue
            rows.append((fecha, str(idc), unidad, cant, cuenta, prec, val,
                         tipo_map.get(unidad)))

        print(f"portafolio.tenencia: {total} filas · excluidas (_aum_filters): {excluidas} "
              f"· a escribir: {len(rows)}")
        if dry:
            print("(--dry-run: no se escribió nada)")
            return 0

        cur.execute(_DDL)
        for ix in _IX:
            cur.execute(ix)
        cur.execute("TRUNCATE aum_corregido")
        # INSERT batcheado
        ins = ("INSERT INTO aum_corregido "
               "(fecha_snapshot,id_cuenta,unidad,cantidad,cuenta,precio,valuacion,tipo_titulo) "
               "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)")
        B = 5000
        for i in range(0, len(rows), B):
            cur.executemany(ins, rows[i:i + B])
        conn.commit()

    print(f"✅ aum_corregido reconstruida: {len(rows)} filas.")
    print("   Para que /aum la use: AUM_SQL_TABLE=aum_corregido + PORTFOLIO_SQL=1 (o ?_engine=sql).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
