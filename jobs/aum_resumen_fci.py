"""aum_resumen_fci.py — pre-materializa Valuaciones.AuMResumenFCI.

Para cada par (fecha_snapshot, unidad) donde `unidad` pertenece a una
CARTERA FCI en Valuaciones.Assets, calcula la suma de valuaciones y el
conteo de cuentas, y upserta un doc en AuMResumenFCI.

Esquema destino (un doc por par):

    {
      "fecha_snapshot": "YYYY-MM-DD",
      "unidad":         "<string>",
      "valuacion_total": <number>,
      "num_cuentas":     <int>,
    }

Clave de upsert: (fecha_snapshot, unidad). Idempotente: se puede correr
N veces para la misma fecha sin duplicar.

Modos:
  --fecha YYYY-MM-DD    procesa solo esa fecha_snapshot
  --backfill            recalcula todas las fechas presentes en AuM
  (sin flag)            usa la fecha_snapshot más reciente en AuM

Se invoca automáticamente al final de jobs.aum y jobs.aum_backfill; el
modo CLI existe para reconstrucción manual.
"""

import argparse

from pymongo import UpdateOne

from core.mongo import get_mongo_client


def _fci_unidades(client):
    return [
        a["unidad"]
        for a in client["Valuaciones"]["Assets"].find(
            {"CARTERA": "CARTERA FCI"}, {"unidad": 1, "_id": 0}
        )
        if a.get("unidad")
    ]


def sync_fecha(client, fecha_snapshot, unidades=None):
    """Recalcula AuMResumenFCI para una fecha_snapshot puntual."""
    col_aum = client["Valuaciones"]["AuM"]
    col_dst = client["Valuaciones"]["AuMResumenFCI"]

    if unidades is None:
        unidades = _fci_unidades(client)
    if not unidades:
        print(f"  {fecha_snapshot}: sin unidades FCI en Assets")
        return 0

    pipeline = [
        {"$match": {"fecha_snapshot": fecha_snapshot,
                    "unidad": {"$in": unidades}}},
        {"$group": {
            "_id":             "$unidad",
            "valuacion_total": {"$sum": "$valuacion"},
            "num_cuentas":     {"$sum": 1},
        }},
    ]
    rows = list(col_aum.aggregate(pipeline))

    # Borrar docs previos de esta fecha que ya no tengan una unidad FCI
    # con posición (ej. se liquidó la tenencia).
    unidades_presentes = [r["_id"] for r in rows]
    col_dst.delete_many({
        "fecha_snapshot": fecha_snapshot,
        "unidad": {"$nin": unidades_presentes},
    })

    if not rows:
        print(f"  {fecha_snapshot}: sin posiciones FCI")
        return 0

    ops = [
        UpdateOne(
            {"fecha_snapshot": fecha_snapshot, "unidad": r["_id"]},
            {"$set": {
                "fecha_snapshot":  fecha_snapshot,
                "unidad":          r["_id"],
                "valuacion_total": r["valuacion_total"],
                "num_cuentas":     r["num_cuentas"],
            }},
            upsert=True,
        )
        for r in rows
    ]
    col_dst.bulk_write(ops, ordered=False)
    print(f"  {fecha_snapshot}: {len(rows)} unidades FCI upserted")
    return len(rows)


def sync_backfill(client):
    """Recalcula AuMResumenFCI para todas las fechas presentes en AuM."""
    unidades = _fci_unidades(client)
    if not unidades:
        print("Backfill abortado: Assets sin unidades FCI.")
        return

    fechas = sorted(client["Valuaciones"]["AuM"].distinct("fecha_snapshot"))
    print(f"Backfill: {len(fechas)} fechas encontradas en AuM")

    total = 0
    for f in fechas:
        total += sync_fecha(client, f, unidades=unidades)
    print(f"\n✅ Backfill completo: {total} upserts totales")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fecha",    help="Procesar solo YYYY-MM-DD")
    ap.add_argument("--backfill", action="store_true",
                    help="Recalcular todas las fechas presentes en AuM")
    args = ap.parse_args()

    client = get_mongo_client()

    if args.backfill:
        sync_backfill(client)
        return

    if args.fecha:
        sync_fecha(client, args.fecha)
        return

    # Sin flag: usar la fecha_snapshot más reciente en AuM
    last = client["Valuaciones"]["AuM"].find_one(
        {}, {"fecha_snapshot": 1}, sort=[("fecha_snapshot", -1)]
    )
    if not last:
        print("AuM vacío, nada para procesar.")
        return
    sync_fecha(client, last["fecha_snapshot"])


if __name__ == "__main__":
    main()
