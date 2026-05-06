"""aum_resumen_fci.py — pre-materializa Valuaciones.AuMResumenFCI.

Para cada fecha_snapshot, agrupa las posiciones FCI por unidad y persiste
UN SOLO doc por fecha (array de unidades dentro). Este esquema minimiza
el transporte por cursor: N fechas en vez de N×M (fecha × unidad).

Esquema destino:

    {
      "_id": "YYYY-MM-DD",           # = fecha_snapshot (único por fecha)
      "fecha_snapshot": "YYYY-MM-DD",
      "unidades": [
        {"unidad": "<str>", "valuacion_total": <num>, "num_cuentas": <int>},
        ...
      ]
    }

Idempotente: `replace_one` por `_id`. Se invoca automáticamente al final de
jobs.aum y jobs.aum_backfill; el modo CLI existe para reconstrucción manual.

Modos:
  --fecha YYYY-MM-DD    procesa solo esa fecha_snapshot
  --backfill            dropea y recalcula todas las fechas presentes en AuM
  (sin flag)            usa la fecha_snapshot más reciente en AuM
"""

import argparse

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

    # Excluye cuentas de trading propias (255 = ACA Valores Intermediación)
    # — las posiciones intra-día rompen los totales del AuM real. Mismo set
    # que `_EXCLUDED_FROM_AUM_VIEW` en api/services/portfolio.py.
    pipeline = [
        {"$match": {
            "fecha_snapshot": fecha_snapshot,
            "unidad":         {"$in": unidades},
            "id_cuenta":      {"$nin": ["255"]},
        }},
        {"$group": {
            "_id":             "$unidad",
            "valuacion_total": {"$sum": "$valuacion"},
            "num_cuentas":     {"$sum": 1},
        }},
    ]
    rows = list(col_aum.aggregate(pipeline))

    if not rows:
        col_dst.delete_one({"_id": fecha_snapshot})
        print(f"  {fecha_snapshot}: sin posiciones FCI (doc borrado)")
        return 0

    doc = {
        "_id":            fecha_snapshot,
        "fecha_snapshot": fecha_snapshot,
        "unidades": [
            {
                "unidad":          r["_id"],
                "valuacion_total": r["valuacion_total"],
                "num_cuentas":     r["num_cuentas"],
            }
            for r in rows
        ],
    }
    col_dst.replace_one({"_id": fecha_snapshot}, doc, upsert=True)
    print(f"  {fecha_snapshot}: {len(rows)} unidades FCI → 1 doc")
    return len(rows)


def sync_backfill(client):
    """Dropea AuMResumenFCI y lo reconstruye desde cero leyendo AuM."""
    unidades = _fci_unidades(client)
    if not unidades:
        print("Backfill abortado: Assets sin unidades FCI.")
        return

    col_dst = client["Valuaciones"]["AuMResumenFCI"]
    col_dst.drop()
    print("Colección AuMResumenFCI dropeada. Reconstruyendo...")

    fechas = sorted(client["Valuaciones"]["AuM"].distinct("fecha_snapshot"))
    print(f"Backfill: {len(fechas)} fechas encontradas en AuM")

    total_fechas = 0
    for f in fechas:
        n = sync_fecha(client, f, unidades=unidades)
        if n > 0:
            total_fechas += 1
    print(f"\n✅ Backfill completo: {total_fechas} fechas con datos FCI")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fecha",    help="Procesar solo YYYY-MM-DD")
    ap.add_argument("--backfill", action="store_true",
                    help="Dropear y recalcular todas las fechas presentes en AuM")
    args = ap.parse_args()

    client = get_mongo_client()

    if args.backfill:
        sync_backfill(client)
        return

    if args.fecha:
        sync_fecha(client, args.fecha)
        return

    last = client["Valuaciones"]["AuM"].find_one(
        {}, {"fecha_snapshot": 1}, sort=[("fecha_snapshot", -1)]
    )
    if not last:
        print("AuM vacío, nada para procesar.")
        return
    sync_fecha(client, last["fecha_snapshot"])


if __name__ == "__main__":
    main()
