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


def run(dry: bool = False):
    hoy = date.today().isoformat()

    # ⚠️ La regla («a menos de 2 días hábiles sale») vive en
    # `core.curvas_sql`, NO acá: el AV AGENT mira el mismo master y necesita
    # poder preguntarla. Cuando estaba adentro de este `run()`, el agente exigía
    # dar de alta los bonos que este job acababa de borrar (REGLA #9).
    habiles = curvas_sql.calendario_habil()
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
        bdays = curvas_sql.dias_habiles_entre(hoy, venc, habiles)
        if curvas_sql.sale_del_master(venc, habiles, hoy):
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
        cur.execute("DELETE FROM mercado.curvas WHERE ticker = ANY(%s)", (a_borrar,))
        deleted = cur.rowcount or 0
    print(f"\nEliminados {deleted} docs de mercado.curvas (hoy={hoy}).")


if __name__ == "__main__":
    from core.job_runs import JobRunLogger
    dry = "--dry" in sys.argv
    with JobRunLogger("cleanup_curvas") as jr:
        jr.set_stat("dry", dry)
        run(dry=dry)
