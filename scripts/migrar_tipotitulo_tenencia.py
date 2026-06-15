"""scripts/migrar_tipotitulo_tenencia.py — agrega `tipo_titulo` a portafolio.tenencia
y lo backfillea desde la tabla SQL `aum` (que sí lo tiene, espejado de Mongo).

Por qué: el motor de PnL usa `tipoTitulo` para normalizar el precio (÷100 renta fija).
La tabla `aum` (espejo de Mongo) lo tiene; `portafolio.tenencia` no. Para que pnl_sql
pueda leer tenencia en vez de `aum`, tenencia necesita la columna. El writer diario ya
la llena de ahora en más; este script crea la columna y rellena el HISTÓRICO.

Match aum → tenencia por (fecha_snapshot = fecha, id_cuenta, unidad).

    python -m scripts.migrar_tipotitulo_tenencia            # PREVIEW (no escribe)
    python -m scripts.migrar_tipotitulo_tenencia --commit   # crea columna + backfill

Idempotente. Re-correrlo solo actualiza filas con tipo_titulo NULL.
"""
from __future__ import annotations

import sys

from core.postgres import get_pool


def main() -> None:
    commit = "--commit" in sys.argv
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("ALTER TABLE portafolio.tenencia ADD COLUMN IF NOT EXISTS tipo_titulo text")
        cur.execute("SELECT count(*) FROM portafolio.tenencia WHERE tipo_titulo IS NULL")
        faltan = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM aum WHERE tipo_titulo IS NOT NULL")
        en_aum = cur.fetchone()[0]
        print("\n=== backfill tipo_titulo en portafolio.tenencia ===")
        print(f"   filas tenencia con tipo_titulo NULL : {faltan}")
        print(f"   filas en tabla aum con tipo_titulo  : {en_aum}")

        if not commit:
            conn.rollback()   # deshace el ADD COLUMN del preview (no toca nada)
            print("\n[PREVIEW] no se escribió nada. Repetí con --commit para aplicar.\n")
            return

        cur.execute(
            "UPDATE portafolio.tenencia t SET tipo_titulo = a.tipo_titulo "
            "FROM aum a "
            "WHERE a.fecha_snapshot = t.fecha AND a.id_cuenta = t.id_cuenta "
            "AND a.unidad = t.unidad AND t.tipo_titulo IS NULL AND a.tipo_titulo IS NOT NULL")
        n = cur.rowcount
        conn.commit()
        cur.execute("SELECT count(*) FROM portafolio.tenencia WHERE tipo_titulo IS NULL")
        siguen_null = cur.fetchone()[0]
        print(f"\n[COMMIT] filas actualizadas: {n}  ·  siguen NULL: {siguen_null} "
              f"(no estaban en `aum` — históricas o cash sin tipoTitulo)\n")


if __name__ == "__main__":
    main()
