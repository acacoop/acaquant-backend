import sys
import os
from pymongo import UpdateOne

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from mongo_manager import get_mongo_client


def inferir_moneda(condiciones):
    if not condiciones:
        return ""
    c = condiciones.upper()
    if "USD" in c:
        return "USD"
    if "ARS" in c:
        return "ARS"
    return ""


def main():
    client = get_mongo_client()
    col = client["CashFlow"]["Flujo"]

    total = col.count_documents({})
    print(f"Total docs en CashFlow.Flujo: {total}\n")

    ops = []
    procesados = 0

    for doc in col.find({}, {"_id": 1, "condiciones": 1}):
        moneda = inferir_moneda(doc.get("condiciones", ""))
        ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": {"moneda": moneda}}))
        procesados += 1

        if len(ops) == 1000:
            col.bulk_write(ops, ordered=False)
            print(f"  {procesados}/{total} procesados...")
            ops = []

    if ops:
        col.bulk_write(ops, ordered=False)

    print(f"\n✅ {procesados} documentos actualizados con campo 'moneda'")
    client.close()


if __name__ == "__main__":
    main()
