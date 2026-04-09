import sys
import os
import json
import requests
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import config
from mongo_manager import get_mongo_client

AUTH_URL  = "https://aca.aunesa.com/Irmo/api/login"
INFOS_URL = "https://aca.aunesa.com/Irmo/api/operaciones/informes"

FECHA_DESDE = "01/04/2026"


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
    fecha_hasta = date.today().strftime("%d/%m/%Y")

    # ── 1. Cuentas desde Mongo ────────────────────────────────────────────────
    client = get_mongo_client()
    docs = list(client["CashFlow"]["Contrapartes"].find(
        {"cuenta": {"$exists": True, "$ne": ""}},
        {"_id": 0, "contraparte": 1, "denominacion": 1, "cuenta": 1}
    ))
    client.close()
    print(f"{len(docs)} contrapartes con cuenta asignada\n")

    # ── 2. Auth ───────────────────────────────────────────────────────────────
    print("Autenticando...")
    headers = autenticar()
    print("Auth OK\n")

    # ── 3. Consultar operaciones por cuenta ───────────────────────────────────
    todos = []

    for doc in docs:
        cp        = doc["contraparte"]
        cuenta_id = doc["cuenta"]

        params = {
            "cuenta":     cuenta_id,
            "fechaDesde": FECHA_DESDE,
            "fechaHasta": fecha_hasta,
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

            # Anotar de qué contraparte vino
            for r in data:
                r["_contraparte"] = cp

            print(f"  [{cuenta_id}] {cp} → {len(data)} operaciones")
            todos.extend(data)

        except Exception as e:
            print(f"  [{cuenta_id}] {cp} → ERROR: {e}")

    # ── 4. Resultado consolidado ──────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"TOTAL: {len(todos)} operaciones ({FECHA_DESDE} → {fecha_hasta})")
    print(f"{'='*60}\n")

    if todos:
        print("=== Primeros 3 registros ===")
        for r in todos[:3]:
            print(json.dumps(r, indent=2, ensure_ascii=False))
            print("---")

        print(f"\n=== Campos disponibles ===")
        print(list(todos[0].keys()))

        # Resumen por contraparte
        from collections import Counter
        conteo = Counter(r["_contraparte"] for r in todos)
        print(f"\n=== Operaciones por contraparte ===")
        for cp, n in sorted(conteo.items(), key=lambda x: -x[1]):
            print(f"  {cp:<25} {n}")


if __name__ == "__main__":
    main()
