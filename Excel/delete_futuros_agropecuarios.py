"""
delete_futuros_agropecuarios.py — Borra de CashFlow.Operaciones todos los registros
de Futuros Agropecuarios (Compra y Venta).

Uso:
    /root/TradingAV/venv/bin/python /root/TradingAV/Excel/delete_futuros_agropecuarios.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from mongo_manager import get_mongo_client

TIPOS = [
    "Futuros Agropecuarios - Compra",
    "Futuros Agropecuarios - Venta",
]

if __name__ == "__main__":
    client = get_mongo_client()
    col = client["CashFlow"]["Operaciones"]

    total_antes = col.count_documents({})
    print(f"Registros antes: {total_antes}")

    result = col.delete_many({"Tipo de operación": {"$in": TIPOS}})
    print(f"🗑️  Eliminados: {result.deleted_count}")
    print(f"Registros restantes: {total_antes - result.deleted_count}")

    client.close()
