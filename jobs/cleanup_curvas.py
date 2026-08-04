"""Limpieza de instrumentos vencidos en mercado.curvas (SQL-native).

Elimina docs cuya fecha_vencimiento está a menos de 2 días hábiles de hoy.
Usa mercado.dias_habiles (vía core.calendario) como calendario hábil argentino.

SQL-native (decomiso Mongo): el master vive en `mercado.curvas` (Postgres). El
borrado es por `ticker_corto` (PK) — no hay `_id`.

Uso:
    python -m jobs.cleanup_curvas          # ejecución normal
    python -m jobs.cleanup_curvas --dry    # solo muestra qué borraría
"""
import sys
from datetime import date

from core import curvas_sql
from core.postgres import get_pool


def dias_habiles_entre(hoy_iso: str, venc_iso: str, habiles: set[str]) -> int:
    """Cuenta días hábiles entre hoy (excl) y vencimiento (incl).

    Retorna 0 si vencimiento <= hoy.
    """
    if venc_iso <= hoy_iso:
        return 0
    return sum(1 for d in habiles if hoy_iso < d <= venc_iso)


def run(dry: bool = False):
    hoy = date.today().isoformat()

    # Cargar calendario hábil (SQL-only: mercado.dias_habiles)
    from core.calendario import dias_habiles_ordenados
    habiles = set(dias_habiles_ordenados())
    if not habiles:
        print("ERROR: mercado.dias_habiles vacía. Ejecutar jobs.dias_habiles primero.")
        return

    # Buscar docs a eliminar (master chico ~cientos → traer todos y filtrar).
    a_borrar = []
    for doc in curvas_sql.cargar_todos():
        venc = doc.get("fecha_vencimiento")
        tc = doc.get("ticker_corto")
        if not venc or not tc:
            continue
        venc = str(venc)[:10]
        bdays = dias_habiles_entre(hoy, venc, habiles)
        if bdays < 2:
            a_borrar.append(tc)
            print(f"  {'[DRY] ' if dry else ''}BORRAR  {tc}  "
                  f"vence={venc}  dias_habiles={bdays}")

    if not a_borrar:
        print(f"Nada que limpiar (hoy={hoy}).")
        return

    if dry:
        print(f"\n[DRY RUN] Se borrarían {len(a_borrar)} docs de mercado.curvas.")
        return

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM mercado.curvas WHERE ticker_corto = ANY(%s)", (a_borrar,))
        deleted = cur.rowcount or 0
    print(f"\nEliminados {deleted} docs de mercado.curvas (hoy={hoy}).")


if __name__ == "__main__":
    from core.job_runs import JobRunLogger
    dry = "--dry" in sys.argv
    with JobRunLogger("cleanup_curvas") as jr:
        jr.set_stat("dry", dry)
        run(dry=dry)
