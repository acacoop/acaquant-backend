"""Limpieza de instrumentos vencidos en Trading.Curvas.

Elimina docs cuya fecha_vencimiento está a menos de 2 días hábiles de hoy.
Usa Trading.DiasHabiles como calendario hábil argentino.

Uso:
    python -m jobs.cleanup_curvas          # ejecución normal
    python -m jobs.cleanup_curvas --dry    # solo muestra qué borraría
"""
import sys
from datetime import date

from core.mongo import get_mongo_client


def dias_habiles_entre(hoy_iso: str, venc_iso: str, habiles: set[str]) -> int:
    """Cuenta días hábiles entre hoy (excl) y vencimiento (incl).

    Retorna 0 si vencimiento <= hoy.
    """
    if venc_iso <= hoy_iso:
        return 0
    return sum(1 for d in habiles if hoy_iso < d <= venc_iso)


def run(dry: bool = False):
    client = get_mongo_client()
    db = client["Trading"]

    hoy = date.today().isoformat()

    # Cargar calendario hábil
    habiles = {d["fecha"] for d in db["DiasHabiles"].find({}, {"fecha": 1, "_id": 0})}
    if not habiles:
        print("ERROR: Trading.DiasHabiles vacío. Ejecutar jobs.dias_habiles primero.")
        return

    # Buscar docs a eliminar
    curvas = list(db["Curvas"].find({}, {"_id": 1, "ticker_corto": 1, "fecha_vencimiento": 1}))
    a_borrar = []
    for doc in curvas:
        venc = doc.get("fecha_vencimiento")
        if not venc:
            continue
        bdays = dias_habiles_entre(hoy, venc, habiles)
        if bdays < 2:
            a_borrar.append(doc)
            print(f"  {'[DRY] ' if dry else ''}BORRAR  {doc['ticker_corto']}  "
                  f"vence={venc}  dias_habiles={bdays}")

    if not a_borrar:
        print(f"Nada que limpiar (hoy={hoy}, {len(curvas)} instrumentos activos).")
        return

    if dry:
        print(f"\n[DRY RUN] Se borrarían {len(a_borrar)} docs de Trading.Curvas.")
        return

    ids = [d["_id"] for d in a_borrar]
    result = db["Curvas"].delete_many({"_id": {"$in": ids}})
    print(f"\nEliminados {result.deleted_count} docs de Trading.Curvas (hoy={hoy}).")


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    run(dry=dry)
