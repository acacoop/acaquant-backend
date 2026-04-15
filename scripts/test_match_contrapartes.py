import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import config
from core.mongo import get_mongo_client

AUTH_URL    = "https://aca.aunesa.com/Irmo/api/login"
CUENTAS_URL = "https://aca.aunesa.com/Irmo/api/cuentas/listadoCuentas"


def autenticar():
    resp = requests.post(
        AUTH_URL,
        json={
            "clientId": config.AUNESA_CLIENT_ID,
            "username": config.AUNESA_USERNAME,
            "password": config.AUNESA_PASSWORD,
        },
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    token = resp.json().get("token")
    return {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}


def main():
    # ── 1. Listado de cuentas Aunesa ──────────────────────────────────────────
    print("Autenticando...")
    headers = autenticar()
    print("Auth OK\n")

    print("Obteniendo listado de cuentas Aunesa...")
    resp = requests.get(CUENTAS_URL, headers=headers, timeout=60)
    resp.raise_for_status()
    cuentas = resp.json()
    print(f"{len(cuentas)} cuentas totales en Aunesa\n")

    # dict denominacion_upper → {id, denominacion, tipo, estado}
    aunesa_map = {
        c["denominacion"].upper().strip(): c
        for c in cuentas
        if c.get("denominacion")
    }

    # ── 2. Contrapartes desde MongoDB ─────────────────────────────────────────
    client = get_mongo_client()
    col = client["CashFlow"]["Contrapartes"]
    docs = list(col.find({}, {"contraparte": 1, "denominacion": 1}))
    print(f"{len(docs)} contrapartes en MongoDB CashFlow.Contrapartes\n")

    # ── 3. Matching + escritura ───────────────────────────────────────────────
    print(f"{'CONTRAPARTE':<20} {'DENOMINACION MONGO':<45} {'ID AUNESA':<10} {'RESULTADO'}")
    print("-" * 110)

    sin_match = []

    for doc in sorted(docs, key=lambda d: d.get("contraparte", "")):
        _id   = doc["_id"]
        cp    = doc.get("contraparte", "")
        denom = doc.get("denominacion", "")
        denom_up = denom.upper().strip()
        cuenta_id = ""

        # Exacto
        if denom_up in aunesa_map:
            cuenta_id = str(aunesa_map[denom_up]["id"])
            print(f"{cp:<20} {denom:<45} {cuenta_id:<10} exacto")
        else:
            # Parcial
            parciales = [v for k, v in aunesa_map.items() if denom_up in k or k in denom_up]
            if parciales:
                cuenta_id = str(parciales[0]["id"])
                print(f"{cp:<20} {denom:<45} {cuenta_id:<10} parcial → {parciales[0]['denominacion']}")
            else:
                sin_match.append((cp, denom))
                print(f"{cp:<20} {denom:<45} {'—':<10} SIN MATCH")

        col.update_one({"_id": _id}, {"$set": {"cuenta": cuenta_id}})

    print(f"\n✅ Campo 'cuenta' actualizado en {len(docs)} documentos.")

    if sin_match:
        print(f"\n⚠️  {len(sin_match)} sin match (cuenta quedó vacía):")
        for cp, d in sin_match:
            print(f"  {cp} → '{d}'")



if __name__ == "__main__":
    main()
