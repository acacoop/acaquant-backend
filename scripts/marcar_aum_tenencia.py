"""scripts/marcar_aum_tenencia.py — marca cada fila de portafolio.tenencia como AuM sí/no.

Agrega (si no existe) la columna `aum` a la tabla `portafolio.tenencia` y la llena:
  - `aum = 'si'`  → la fila CUENTA como AuM (la vista /aum la usa).
  - `aum = 'no'`  → hay que excluirla (CDC/OTC/cash/contrapartes) — la vista la ignora.

El criterio es el MISMO filtro ya probado del AuM (`jobs._aum_filters.is_excluded`),
no se inventa nada. Como el veredicto depende de (id_cuenta, cuenta, unidad) y no de
la fecha, se calcula sobre las combinaciones DISTINTAS (rápido) y se aplica a todas
las filas.

Idempotente: re-correrlo recalcula desde cero (pone todo 'si' y baja a 'no' lo
excluido). Read-only la parte de cálculo; el ALTER/UPDATE solo corren sin --dry-run.

Uso:
    python -m scripts.marcar_aum_tenencia --dry-run   # cuenta si/no, no escribe
    python -m scripts.marcar_aum_tenencia             # agrega y llena la columna
"""
from __future__ import annotations

import sys

from core.postgres import get_pool
from jobs._aum_filters import (
    is_excluded,
    load_contrapartes_id_cuentas,
    load_contrapartes_names,
)


def main() -> int:
    dry = "--dry-run" in sys.argv
    cont_ids = load_contrapartes_id_cuentas()
    cont_names = load_contrapartes_names()

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT id_cuenta, cuenta, unidad FROM portafolio.tenencia")
        combos = cur.fetchall()

        excluidos = [
            (str(idc), cuenta, unidad)
            for idc, cuenta, unidad in combos
            if is_excluded(cuenta, unidad, id_cuenta=str(idc),
                           contrapartes_ids=cont_ids, contrapartes_names=cont_names)
        ]
        print(f"combinaciones (cuenta,especie) distintas: {len(combos)}  ·  "
              f"marcadas 'no' (excluidas): {len(excluidos)}  ·  'si': {len(combos) - len(excluidos)}")

        if dry:
            print("(--dry-run: no se tocó la tabla)")
            return 0

        # 1) columna
        cur.execute("ALTER TABLE portafolio.tenencia ADD COLUMN IF NOT EXISTS aum text")
        # 2) por default todo cuenta como AuM
        cur.execute("UPDATE portafolio.tenencia SET aum = 'si'")
        # 3) bajar a 'no' las combinaciones excluidas (vía temp table + join)
        cur.execute("CREATE TEMP TABLE _excl (id_cuenta text, cuenta text, unidad text) "
                    "ON COMMIT DROP")
        cur.executemany("INSERT INTO _excl (id_cuenta, cuenta, unidad) VALUES (%s,%s,%s)",
                        excluidos)
        cur.execute(
            "UPDATE portafolio.tenencia t SET aum = 'no' FROM _excl e "
            "WHERE t.id_cuenta IS NOT DISTINCT FROM e.id_cuenta "
            "AND t.cuenta IS NOT DISTINCT FROM e.cuenta "
            "AND t.unidad IS NOT DISTINCT FROM e.unidad")
        conn.commit()

        cur.execute("SELECT aum, count(*) FROM portafolio.tenencia GROUP BY aum")
        conteo = {r[0]: r[1] for r in cur.fetchall()}
    print(f"✅ columna `aum` lista en portafolio.tenencia → {conteo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
