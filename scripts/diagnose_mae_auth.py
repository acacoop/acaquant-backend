"""Diagnóstico de auth MAE: prueba variantes del header + UA.

El 403 del primer smoke test vino del WAF (Incapsula), no del backend.
Este script prueba 5 combinaciones para identificar exactamente qué
espera MAE:

    V1 — x-api-key           (lo que dice la doc de MAE MarketData)
    V2 — Ocp-Apim-Subscription-Key  (Azure API Management, típico en govtech)
    V3 — Authorization: Bearer <key>
    V4 — api-key              (otro naming común)
    V5 — Authorization: ApiKey <key>

Cada variante usa User-Agent de navegador (Chrome) + Accept-Language
para no disparar bot-detection.

Uso:
    /root/TradingAV/venv/bin/python -m scripts.diagnose_mae_auth
"""
from __future__ import annotations

import sys
from typing import Any

import requests

from config import MAE_API_KEY, MAE_ENV

_BASE = (
    "https://api.mae.com.ar" if MAE_ENV != "uat" else "https://apiuat.mae.com.ar"
)
_PATH = "/api/v1/mercado/cotizaciones/repo"
_URL = f"{_BASE}{_PATH}"

_BROWSER_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Connection": "keep-alive",
}


def _probar(label: str, auth_headers: dict[str, str]) -> None:
    headers = {**_BROWSER_HEADERS, **auth_headers}
    print(f"\n── {label} ".ljust(78, "─"))
    print(f"  auth headers: {list(auth_headers.keys())}")
    try:
        r = requests.get(_URL, headers=headers, params={"pageNumber": 1}, timeout=20)
    except requests.RequestException as e:
        print(f"  ✗ red falló: {e}")
        return
    print(f"  status: {r.status_code}")
    text = r.text[:400].replace("\n", " ")
    if "Incapsula" in r.text or "_Incapsula_Resource" in r.text:
        print("  → bloqueo WAF (Incapsula). El server ni vio la request.")
    print(f"  body (400 chars): {text}")
    # Si es 200, intentar parsear
    if r.status_code == 200:
        try:
            data: Any = r.json()
            if isinstance(data, list):
                print(f"  ✓✓✓ LISTA con {len(data)} items — PRIMERO:")
                if data:
                    print(f"    {data[0]}")
            else:
                print(f"  ✓ JSON: {type(data).__name__} → keys={list(data.keys()) if isinstance(data, dict) else 'n/a'}")
        except ValueError:
            print("  ✗ 200 pero no parsea como JSON (raro)")


def main() -> int:
    if not MAE_API_KEY:
        print("✗ MAE_API_KEY vacía. Agregala al .env.")
        return 1

    key_preview = f"{MAE_API_KEY[:4]}...{MAE_API_KEY[-4:]}" if len(MAE_API_KEY) > 8 else "<muy corta>"
    print("=" * 78)
    print(f" MAE auth diagnosis — URL: {_URL}")
    print(f" MAE_API_KEY preview: {key_preview}  (len={len(MAE_API_KEY)})")
    print(f" MAE_ENV: {MAE_ENV}")
    print("=" * 78)

    _probar("V1: x-api-key (lo que dice la doc)",
            {"x-api-key": MAE_API_KEY})
    _probar("V2: Ocp-Apim-Subscription-Key (Azure APIM)",
            {"Ocp-Apim-Subscription-Key": MAE_API_KEY})
    _probar("V3: Authorization: Bearer",
            {"Authorization": f"Bearer {MAE_API_KEY}"})
    _probar("V4: api-key (con guion, minúscula)",
            {"api-key": MAE_API_KEY})
    _probar("V5: Authorization: ApiKey",
            {"Authorization": f"ApiKey {MAE_API_KEY}"})
    _probar("V6: X-API-Key (mayúsculas)",
            {"X-API-Key": MAE_API_KEY})

    print("\n" + "=" * 78)
    print(" Pegame el output: buscá cuál variante dio 200 o un 4xx-app (no Incapsula).")
    print(" Un 401/403 CON JSON es mejor que Incapsula (significa que el server lo vio).")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
