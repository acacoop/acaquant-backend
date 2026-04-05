"""
backfill_assets.py — Script one-off.

Toma todas las unidades únicas de Valuaciones.AuM y las sincroniza
hacia Valuaciones.Assets:
  - Si la unidad no existe: la crea con los 6 campos vacíos
  - Si ya existe: agrega solo los campos que falten (sin pisar los que ya tienen valor)

Uso:
    /root/TradingAV/venv/bin/python /root/TradingAV/backfill_assets.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from mongo_manager import get_mongo_client

CAMPOS_REQUERIDOS = ["CALIFICACION", "CARTERA", "CLASE_ACTIVO", "EMISOR", "TICKER", "VENCIMIENTO"]


def sincronizar_assets(col_aum, col_assets):
    # Obtener todas las unidades únicas en AuM
    unidades = col_aum.distinct("unidad")
    print(f"Unidades únicas en AuM: {len(unidades)}")

    nuevas = 0
    actualizadas = 0

    for unidad in unidades:
        result = col_assets.update_one(
            {"unidad": unidad},
            [{"$set": {
                "unidad":       unidad,
                "CALIFICACION": {"$ifNull": ["$CALIFICACION", ""]},
                "CARTERA":      {"$ifNull": ["$CARTERA",      ""]},
                "CLASE_ACTIVO": {"$ifNull": ["$CLASE_ACTIVO", ""]},
                "EMISOR":       {"$ifNull": ["$EMISOR",       ""]},
                "TICKER":       {"$ifNull": ["$TICKER",       ""]},
                "VENCIMIENTO":  {"$ifNull": ["$VENCIMIENTO",  ""]},
            }}],
            upsert=True,
        )
        if result.upserted_id:
            nuevas += 1
        else:
            actualizadas += 1

    return nuevas, actualizadas


def run():
    client = get_mongo_client()
    col_aum    = client["Valuaciones"]["AuM"]
    col_assets = client["Valuaciones"]["Assets"]

    print("Iniciando backfill Assets desde AuM...")
    nuevas, actualizadas = sincronizar_assets(col_aum, col_assets)

    total = col_assets.count_documents({})
    print(f"\nListo.")
    print(f"  Nuevas insertadas:     {nuevas}")
    print(f"  Existentes revisadas:  {actualizadas}")
    print(f"  Total en Assets ahora: {total}")

    client.close()


if __name__ == "__main__":
    run()
