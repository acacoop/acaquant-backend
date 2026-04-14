"""
main_aum_resumen.py — Pre-une Valuaciones.AuM con Valuaciones.Assets
y guarda el resultado en Valuaciones.AuMResumen.

Ejecución normal (solo la fecha de hoy, cron nightly):
    python Excel/main_aum_resumen.py

Backfill completo (todas las fechas históricas):
    python Excel/main_aum_resumen.py --backfill
"""

import argparse
from datetime import date
from pymongo import UpdateOne
from mongo_manager import get_mongo_client


def run(backfill=False):
    client = get_mongo_client()
    db = client["Valuaciones"]

    # Cargar Assets como dict de lookup {unidad -> campos}
    assets = {
        d["unidad"]: {
            "CARTERA":      d.get("CARTERA", ""),
            "EMISOR":       d.get("EMISOR", ""),
            "TICKER":       d.get("TICKER", ""),
            "CLASE_ACTIVO": d.get("CLASE_ACTIVO", ""),
        }
        for d in db["Assets"].find(
            {},
            {"_id": 0, "unidad": 1, "CARTERA": 1, "EMISOR": 1, "TICKER": 1, "CLASE_ACTIVO": 1}
        )
    }
    print(f"Assets cargados: {len(assets)}")

    # Filtro de fecha: solo hoy en modo normal, todo en backfill
    query = {} if backfill else {"fecha_snapshot": date.today().isoformat()}

    aum_docs = list(db["AuM"].find(
        query,
        {"_id": 0, "id_cuenta": 1, "cuenta": 1, "unidad": 1,
         "valuacion": 1, "cantidad": 1, "fecha_snapshot": 1}
    ))
    print(f"AuM docs a procesar: {len(aum_docs)}")

    if not aum_docs:
        print("Sin datos para procesar.")
        return

    ops = []
    for doc in aum_docs:
        unidad = doc.get("unidad", "")
        asset  = assets.get(unidad, {})
        resumen = {
            "fecha_snapshot": doc["fecha_snapshot"],
            "id_cuenta":      doc.get("id_cuenta"),
            "cuenta":         doc.get("cuenta", ""),
            "unidad":         unidad,
            "valuacion":      float(doc.get("valuacion") or 0),
            "cantidad":       float(doc.get("cantidad") or 0),
            "CARTERA":        asset.get("CARTERA", ""),
            "EMISOR":         asset.get("EMISOR", ""),
            "TICKER":         asset.get("TICKER", ""),
            "CLASE_ACTIVO":   asset.get("CLASE_ACTIVO", ""),
        }
        ops.append(UpdateOne(
            {"fecha_snapshot": resumen["fecha_snapshot"],
             "id_cuenta":      resumen["id_cuenta"],
             "unidad":         resumen["unidad"]},
            {"$set": resumen},
            upsert=True
        ))

    # Bulk write en batches de 1000
    BATCH = 1000
    total_afectados = 0
    for i in range(0, len(ops), BATCH):
        result = db["AuMResumen"].bulk_write(ops[i:i+BATCH], ordered=False)
        total_afectados += result.upserted_count + result.modified_count

    print(f"AuMResumen: {len(ops)} procesados, {total_afectados} afectados.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true",
                        help="Procesar todas las fechas históricas, no solo hoy")
    args = parser.parse_args()
    run(backfill=args.backfill)
