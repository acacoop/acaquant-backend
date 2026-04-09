"""
delete_operaciones.py — Borra de CashFlow.Operaciones registros por criterios:
  1. Tipo de operación: Futuros Agropecuarios (Compra/Venta)
  2. Denominación exacta: ACA VALORES S.A., ASOCIACION DE COOPERATIVAS..., A.C.A. C.D.C.
  3. Denominación contiene la palabra CDC

Uso:
    /root/TradingAV/venv/bin/python /root/TradingAV/Excel/delete_futuros_agropecuarios.py
"""

import sys
import os
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from mongo_manager import get_mongo_client

TIPOS = [
    "Futuros Agropecuarios - Compra",
    "Futuros Agropecuarios - Venta",
]

DENOMINACIONES_EXACTAS = [
    "ACA VALORES S.A.",
    "ASOCIACION DE COOPERATIVAS ARGENTINAS COOP LTDA",
    "A.C.A. C.D.C.",
]

if __name__ == "__main__":
    client = get_mongo_client()
    col = client["CashFlow"]["Operaciones"]

    total_antes = col.count_documents({})
    print(f"Registros antes: {total_antes}")

    filtro = {"$or": [
        {"Tipo de operación": {"$in": TIPOS}},
        {"Denominación": {"$in": DENOMINACIONES_EXACTAS}},
        {"Denominación": {"$regex": "CDC", "$options": "i"}},
        # Suscripción/Suscripcion y Rescate/Réscate con o sin acento
        {"Tipo de operación": {"$regex": "suscripci", "$options": "i"}},
        {"Tipo de operación": {"$regex": "rescate",   "$options": "i"}},
    ]}

    a_borrar = col.count_documents(filtro)
    print(f"Registros a eliminar: {a_borrar}")

    result = col.delete_many(filtro)
    print(f"🗑️  Eliminados: {result.deleted_count}")
    print(f"Registros restantes: {total_antes - result.deleted_count}")

    client.close()
