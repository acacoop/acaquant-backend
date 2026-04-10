import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from mongo_manager import get_mongo_client

TIPOS_A_BORRAR = [
    "Concurrencia - Caución colocadora (Apertura)",
    "Concurrencia - Caución colocadora (Cierre)",
    "Futuros Financieros - Compra",
    "Futuros Financieros - Venta",
]

client = get_mongo_client()
col = client["CashFlow"]["Flujo"]

resultado = col.delete_many({"tipoOperacion": {"$in": TIPOS_A_BORRAR}})
print(f"✅ {resultado.deleted_count} documentos eliminados")

# Verificar que no queden
for t in TIPOS_A_BORRAR:
    n = col.count_documents({"tipoOperacion": t})
    print(f"  {t}: {n} restantes")

client.close()
