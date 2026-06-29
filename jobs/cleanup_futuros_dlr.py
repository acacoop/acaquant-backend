"""Limpieza de contratos DLR vencidos en mercado.futuros_dlr_snapshot (SQL).

El motor (engines/futuros_dlr.py) hace write_native (upsert por ticker) y nunca
borra. Al vencer un contrato la fila queda fantasma con la última info que tenía.
Este job la limpia cada mañana antes de que arranque el motor (cadencia análoga a
jobs.cleanup_curvas).

SQL-NATIVE (decomiso Mongo): `Trading.FuturosDLRSnapshot` (Mongo) fue dropeada — la
fuente es `mercado.futuros_dlr_snapshot` (columna `vencimiento` 'YYYYMMDD').

Uso:
    python -m jobs.cleanup_futuros_dlr          # ejecución normal
    python -m jobs.cleanup_futuros_dlr --dry    # solo muestra qué borraría
"""
import sys
from datetime import date

from core.postgres import get_pool


def run(dry: bool = False):
    hoy = date.today().strftime("%Y%m%d")

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, vencimiento, data->>'last_price' "
            "FROM mercado.futuros_dlr_snapshot WHERE vencimiento <= %s",
            (hoy,),
        )
        vencidos = cur.fetchall()

        if not vencidos:
            print(f"Nada que limpiar (hoy={hoy}).")
            return

        for ticker, vto, last in vencidos:
            print(f"  {'[DRY] ' if dry else ''}BORRAR  {ticker}  vence={vto}  last={last}")

        if dry:
            print(f"\n[DRY RUN] Se borrarían {len(vencidos)} filas de futuros_dlr_snapshot.")
            return

        cur.execute(
            "DELETE FROM mercado.futuros_dlr_snapshot WHERE vencimiento <= %s", (hoy,))
        print(f"\nEliminadas {cur.rowcount or 0} filas de futuros_dlr_snapshot (hoy={hoy}).")


if __name__ == "__main__":
    run(dry="--dry" in sys.argv)
