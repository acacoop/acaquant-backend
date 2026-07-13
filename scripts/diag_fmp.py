"""diag_fmp.py — READ-ONLY: por qué FMP rechaza la key (¿key mala o plan sin calendario?).

Prueba 3 cosas y muestra status + cuerpo crudo:
  1. La key: cargada? (largo, enmascarada).
  2. /quote/AAPL — endpoint que el free SÍ tiene → si anda, la key es VÁLIDA.
  3. /economic_calendar (v3) y /stable/economic-calendar — para ver el error
     exacto (FMP suele decir 'requires a paid plan' en el body si es el plan).

Corré:  python -m scripts.diag_fmp
"""
from __future__ import annotations

import requests

from config import FMP_API_KEY


def _probe(url: str, params: dict) -> None:
    p = dict(params)
    p["apikey"] = FMP_API_KEY
    try:
        r = requests.get(url, params=p, timeout=20)
        print(f"  status {r.status_code} · body: {r.text[:300]}")
    except Exception as e:
        print(f"  EXCEPCIÓN: {e}")


def main() -> None:
    k = FMP_API_KEY or ""
    if not k:
        print("FMP_API_KEY NO está cargada (vacía). Revisá el .env y el warning de dotenv (línea 33).")
        return
    print(f"FMP_API_KEY cargada · largo {len(k)} · {k[:4]}…{k[-3:]}\n")

    print("1) /api/v3/quote/AAPL (free — verifica la key):")
    _probe("https://financialmodelingprep.com/api/v3/quote/AAPL", {})

    print("\n2) /api/v3/economic_calendar (el que usa el job):")
    _probe("https://financialmodelingprep.com/api/v3/economic_calendar",
           {"from": "2026-07-13", "to": "2026-07-20"})

    print("\n3) /stable/economic-calendar (endpoint nuevo de FMP):")
    _probe("https://financialmodelingprep.com/stable/economic-calendar",
           {"from": "2026-07-13", "to": "2026-07-20"})

    print("\nLectura: si (1) anda pero (2)/(3) dan 401/403 con 'paid plan' → el "
          "calendario NO está en tu plan free. Si (1) también falla → la key es inválida.")


if __name__ == "__main__":
    main()
