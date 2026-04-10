import sys
import os
import requests
from datetime import date
from pymongo import InsertOne

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import config
from mongo_manager import get_mongo_client

AUTH_URL  = "https://aca.aunesa.com/Irmo/api/login"
INFOS_URL = "https://aca.aunesa.com/Irmo/api/operaciones/informes"

FECHA_DESDE = "01/01/2026"


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


def normalizar_cuenta(cuenta_str):
    """Convierte '020' → 20, '255' → 255 para comparación sin ceros."""
    try:
        return int(str(cuenta_str).strip())
    except (ValueError, TypeError):
        return None


def main():
    fecha_hasta = date.today().strftime("%d/%m/%Y")

    # ── 1. Contrapartes con cuenta desde Mongo ────────────────────────────────
    client = get_mongo_client()
    col_contrapartes = client["CashFlow"]["Contrapartes"]
    col_flujo        = client["CashFlow"]["Flujo"]

    docs = list(col_contrapartes.find(
        {"cuenta": {"$exists": True, "$ne": ""}},
        {"_id": 0, "contraparte": 1, "denominacion": 1, "cuenta": 1}
    ))
    print(f"{len(docs)} contrapartes con cuenta asignada\n")

    # ── 2. Auth ───────────────────────────────────────────────────────────────
    print("Autenticando...")
    headers = autenticar()
    print("Auth OK\n")

    # ── 3. Consultar e insertar por contraparte ───────────────────────────────
    total_insertados = 0

    for doc in docs:
        cp        = doc["contraparte"]
        denom     = doc["denominacion"]
        cuenta_id = normalizar_cuenta(doc["cuenta"])

        if cuenta_id is None:
            print(f"  SKIP {cp} — cuenta inválida: {doc['cuenta']}")
            continue

        params = {
            "cuenta":         cuenta_id,
            "fechaConcDesde": FECHA_DESDE,
            "fechaConcHasta": fecha_hasta,
        }

        try:
            resp = requests.get(INFOS_URL, params=params, headers=headers, timeout=60)

            if resp.status_code == 204:
                print(f"  [{cuenta_id}] {cp} → sin operaciones")
                continue

            if resp.status_code == 401:
                print("  Re-autenticando...")
                headers = autenticar()
                resp = requests.get(INFOS_URL, params=params, headers=headers, timeout=60)

            resp.raise_for_status()
            data = resp.json()

            if not data:
                print(f"  [{cuenta_id}] {cp} → respuesta vacía")
                continue

            # Agregar campo contraparte a cada registro
            for r in data:
                r["contraparte"] = cp

            # Insertar en CashFlow.Flujo
            ops = [InsertOne(r) for r in data]
            col_flujo.bulk_write(ops, ordered=False)

            print(f"  [{cuenta_id}] {cp} → {len(data)} operaciones insertadas")
            total_insertados += len(data)

        except Exception as e:
            print(f"  [{cuenta_id}] {cp} → ERROR: {e}")

    print(f"\n✅ Total insertado en CashFlow.Flujo: {total_insertados} documentos")
    print(f"   Período: {FECHA_DESDE} → {fecha_hasta}")
    client.close()


if __name__ == "__main__":
    main()
