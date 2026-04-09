import sys
import os
import json
import requests
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import config

AUTH_URL  = "https://aca.aunesa.com/Irmo/api/login"
INFOS_URL = "https://aca.aunesa.com/Irmo/api/operaciones/informes"


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
    hoy = date.today().strftime("%d/%m/%Y")
    print(f"Fecha: {hoy}")

    print("Autenticando...")
    headers = autenticar()
    print("Auth OK\n")

    params = {
        "fechaDesde": hoy,
        "fechaHasta": hoy,
    }

    print(f"GET {INFOS_URL}")
    print(f"Params: {params}\n")

    resp = requests.get(INFOS_URL, params=params, headers=headers, timeout=60)
    print(f"Status: {resp.status_code}")

    if resp.status_code == 204:
        print("Sin datos para hoy.")
        return

    resp.raise_for_status()
    data = resp.json()

    print(f"Registros: {len(data)}\n")

    if data:
        print("=== Primeros 3 registros ===")
        for r in data[:3]:
            print(json.dumps(r, indent=2, ensure_ascii=False))
            print("---")

        print("\n=== Campos disponibles ===")
        print(list(data[0].keys()))


if __name__ == "__main__":
    main()
