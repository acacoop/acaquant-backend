"""
test_consolidados.py — Descarga el endpoint consolidadosGenerales para una fecha
y guarda el JSON completo en disco para inspección.

Uso:
    python Excel/test_consolidados.py                    # usa hoy
    python Excel/test_consolidados.py --fecha 08/04/2026
    python Excel/test_consolidados.py --fecha 08/04/2026 --salida mi_respuesta.json
"""

import sys
import os
import json
import argparse
import requests
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import config

AUTH_URL = "https://aca.aunesa.com/Irmo/api/login"
OPS_URL  = "https://aca.aunesa.com/Irmo/api/operaciones/consolidadosGenerales"


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
    print(f"Auth OK — token: {token[:20]}...")
    return {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fecha",  default=None, help="Fecha en formato DD/MM/YYYY (default: hoy ART)")
    parser.add_argument("--salida", default=None, help="Archivo de salida (default: consolidados_FECHA.json)")
    args = parser.parse_args()

    if args.fecha:
        fecha_str = args.fecha
    else:
        hoy_art = (datetime.now(timezone.utc) - timedelta(hours=3)).date()
        fecha_str = hoy_art.strftime("%d/%m/%Y")

    salida = args.salida or f"Excel/consolidados_{fecha_str.replace('/', '-')}.json"

    print(f"Fecha consultada: {fecha_str}")
    headers = autenticar()

    params = {
        "tiposCuenta": "Comitente",
        "concertacionDesde": fecha_str,
        "concertacionHasta": fecha_str,
    }
    resp = requests.get(OPS_URL, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    print(f"Registros recibidos: {len(data)}")

    # Mostrar valores únicos de informacion para orientarse rápido
    informaciones = sorted(set(r.get("informacion", "") for r in data))
    print(f"\nValores únicos de 'informacion' ({len(informaciones)}):")
    for v in informaciones:
        print(f"  {repr(v)}")

    with open(salida, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nJSON completo guardado en: {salida}")
