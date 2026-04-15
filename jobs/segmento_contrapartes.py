"""
set_segmento_contrapartes.py
Agrega/actualiza el campo `segmento` en CashFlow.Contrapartes.

Reglas (en orden de prioridad):
  1. denominacion contiene "FCI"   → "Fondos"
  2. contraparte  contiene "ALYC"  → "ALYC"
  3. denominacion contiene "BANCO" → "Bancos"
  4. Sin match                     → deja el campo vacío / sin tocar
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from core.mongo import get_mongo_client


def inferir_segmento(denominacion, contraparte):
    den = (denominacion or "").upper()
    cp  = (contraparte  or "").upper()

    if "FCI" in den:
        return "Fondos"
    if "ALYC" in cp:
        return "ALYC"
    if "BANCO" in den:
        return "Bancos"
    return None


def main():
    client = get_mongo_client()
    col = client["CashFlow"]["Contrapartes"]

    docs = list(col.find({}, {"_id": 1, "denominacion": 1, "contraparte": 1, "segmento": 1}))
    print(f"{len(docs)} contrapartes encontradas\n")

    actualizados = 0
    sin_match    = []

    for doc in docs:
        seg = inferir_segmento(doc.get("denominacion"), doc.get("contraparte"))
        if seg:
            col.update_one({"_id": doc["_id"]}, {"$set": {"segmento": seg}})
            print(f"  ✅ {doc.get('contraparte','?'):<30} → {seg}")
            actualizados += 1
        else:
            sin_match.append(doc.get("contraparte", "?"))

    print(f"\n✅ {actualizados} docs actualizados")

    if sin_match:
        print(f"\n⚠️  Sin segmento automático ({len(sin_match)}) — asignación manual:")
        print("     Opciones: 1=Fondos  2=ALYC  3=Bancos  Enter=saltar\n")
        opciones = {"1": "Fondos", "2": "ALYC", "3": "Bancos"}
        for cp_nombre in sin_match:
            resp = input(f"  [{cp_nombre}] → ").strip()
            if resp in opciones:
                seg = opciones[resp]
                col.update_one({"contraparte": cp_nombre}, {"$set": {"segmento": seg}})
                print(f"    ✅ {cp_nombre} → {seg}")
                actualizados += 1
            else:
                print("    — saltado")

    print(f"\n✅ Total: {actualizados} docs actualizados")


if __name__ == "__main__":
    main()
