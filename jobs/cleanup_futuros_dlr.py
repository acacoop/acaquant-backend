"""Limpieza de contratos DLR vencidos en Trading.FuturosDLRSnapshot.

El motor (engines/futuros_dlr.py) hace ReplaceOne(upsert=True) por ticker
y nunca borra. Al vencer un contrato el doc queda fantasma con la última
info que tenía. Este job lo limpia cada mañana antes de que arranque el
motor (cadencia análoga a jobs.cleanup_curvas).

Uso:
    python -m jobs.cleanup_futuros_dlr          # ejecución normal
    python -m jobs.cleanup_futuros_dlr --dry    # solo muestra qué borraría
"""
import sys
from datetime import date

from core.mongo import get_mongo_client


def run(dry: bool = False):
    db = get_mongo_client()["Trading"]
    hoy = date.today().strftime("%Y%m%d")

    filtro = {"vencimiento": {"$lte": hoy}}
    vencidos = list(
        db["FuturosDLRSnapshot"].find(
            filtro, {"_id": 0, "ticker": 1, "vencimiento": 1, "last_price": 1},
        )
    )

    if not vencidos:
        print(f"Nada que limpiar (hoy={hoy}).")
        return

    for d in vencidos:
        print(f"  {'[DRY] ' if dry else ''}BORRAR  {d.get('ticker')}  "
              f"vence={d.get('vencimiento')}  last={d.get('last_price')}")

    if dry:
        print(f"\n[DRY RUN] Se borrarían {len(vencidos)} docs de FuturosDLRSnapshot.")
        return

    res = db["FuturosDLRSnapshot"].delete_many(filtro)
    print(f"\nEliminados {res.deleted_count} docs de FuturosDLRSnapshot (hoy={hoy}).")


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    run(dry=dry)
