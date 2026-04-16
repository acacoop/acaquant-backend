"""Test rápido de endpoints de la API.

Uso:
    python -m scripts.test_api                  → prueba contra localhost:8000
    python -m scripts.test_api http://ip:8000   → prueba contra otra URL
"""
import sys
import time

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"

ENDPOINTS = [
    ("GET", "/api/health"),
    ("GET", "/api/cuentas/accionistas"),
    ("GET", "/api/cuentas/contrapartes"),
    ("GET", "/api/operaciones/flujo"),
    ("GET", "/api/operaciones/flujo?contraparte=ADCAP&moneda=ARS"),
]


def test_endpoint(method: str, path: str):
    url = f"{BASE}{path}"
    t0 = time.perf_counter()
    try:
        resp = requests.request(method, url, timeout=10)
        ms = (time.perf_counter() - t0) * 1000
        status = resp.status_code
        try:
            body = resp.json()
        except Exception:
            body = resp.text[:200]

        if isinstance(body, list):
            resumen = f"{len(body)} docs"
            if body:
                print(f"  primer doc: {body[0]}")
        elif isinstance(body, dict):
            resumen = str(body)[:120]
        else:
            resumen = str(body)[:120]

        ok = "OK" if status == 200 else "FAIL"
        print(f"  {ok}  {method} {path}  → {status}  {ms:.0f}ms  {resumen}")
    except requests.ConnectionError:
        print(f"  ERR  {method} {path}  → no se pudo conectar a {url}")
    except Exception as e:
        print(f"  ERR  {method} {path}  → {e}")


if __name__ == "__main__":
    print(f"Testing API en {BASE}\n")
    for method, path in ENDPOINTS:
        test_endpoint(method, path)
    print("\nDone.")
