"""
fix_contraparte_names.py
Script one-off: actualiza el campo `contraparte` en CashFlow.Flujo
usando como fuente de verdad CashFlow.Contrapartes.

Lógica: para cada doc en Contrapartes que tenga `cuenta` asignada,
busca todos los docs en Flujo donde `cuenta == cuenta_id` y
actualiza su campo `contraparte` al nombre actual de Contrapartes.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from mongo_manager import get_mongo_client


def variantes_cuenta(cuenta_val):
    """Devuelve todas las formas posibles del valor para el match en Flujo."""
    raw = str(cuenta_val).strip()
    variants = {raw}
    try:
        variants.add(int(raw))
    except (ValueError, TypeError):
        pass
    try:
        variants.add(float(raw))
    except (ValueError, TypeError):
        pass
    return list(variants)


def main():
    client = get_mongo_client()
    col_contrapartes = client["CashFlow"]["Contrapartes"]
    col_flujo        = client["CashFlow"]["Flujo"]

    contrapartes = list(col_contrapartes.find(
        {"cuenta": {"$exists": True, "$ne": ""}},
        {"_id": 0, "contraparte": 1, "cuenta": 1}
    ))

    print(f"{len(contrapartes)} contrapartes con cuenta asignada\n")

    total_actualizados = 0

    for doc in contrapartes:
        nombre  = doc["contraparte"]
        cuenta  = doc["cuenta"]
        queries = variantes_cuenta(cuenta)

        result = col_flujo.update_many(
            {"cuenta": {"$in": queries}},
            {"$set": {"contraparte": nombre}},
        )

        print(f"  cuenta {str(cuenta):<20} | {nombre:<40} → {result.modified_count} docs actualizados")
        total_actualizados += result.modified_count

    print(f"\n✅ Total: {total_actualizados} docs actualizados en CashFlow.Flujo")
    client.close()


if __name__ == "__main__":
    main()
