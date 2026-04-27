"""Probe criollo del endpoint MAE Forex — corré, mirá, listo.

Uso (desde /root/TradingAV):
    /root/TradingAV/venv/bin/python -m scripts.probe_mae_forex

Loop cada 30s pegando a /MarketData/v1/mercado/cotizaciones/forex.
Hardcodeado a propósito — esto es para verificar que el endpoint responda
con datos reales antes de portar el cliente a core/mae.py.
"""
import os
import sys
import time
from datetime import datetime

import requests

from config import MAE_API_KEY

# ══════════════════════════════════════════════
#  CONFIGURACIÓN API MAE
# ══════════════════════════════════════════════
if not MAE_API_KEY:
    sys.exit("MAE_API_KEY no está en .env — abortando.")

URL      = "https://api.mae.com.ar/MarketData/v1/mercado/cotizaciones/forex"
HEADERS  = {"x-api-key": MAE_API_KEY}
PARAMS   = {"pageNumber": 1}
INTERVAL = 30


def fetch_data() -> list | None:
    try:
        resp = requests.get(URL, headers=HEADERS, params=PARAMS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data if data else None
    except requests.RequestException:
        return None


def display(data: list | None):
    os.system("cls" if os.name == "nt" else "clear")
    now = datetime.now().strftime("%H:%M:%S")

    print(f"{'═' * 50}")
    print(f"  📊 MAE Forex  |  {now}  |  Refresh: {INTERVAL}s")
    print(f"{'═' * 50}\n")

    if not data:
        print("  ⚠️  Sin datos disponibles\n")
        return

    for item in data:
        print(f"  ── {item.get('denominacion', 'N/A')} ──")
        for key, val in item.items():
            if key == "denominacion":
                continue
            print(f"     {key:<25} {val}")
        print()

    print(f"{'═' * 50}")
    print(f"  Total instrumentos: {len(data)}")
    print(f"{'═' * 50}")


def main():
    while True:
        data = fetch_data()
        display(data)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
