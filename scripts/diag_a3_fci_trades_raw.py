"""scripts/diag_a3_fci_trades_raw.py — TradeCaptureReportFCI CRUDO, sin interpretar.

Consulta MÍNIMA basada 100% en el manual (pág. 75-76):

    GET /PosTrade/TradeCaptureReportFCI?DateFrom=YYYYMMDD&DateTo=YYYYMMDD

Parámetros obligatorios: `DateFrom` y `DateTo` (rango de fechas). Por defecto
ambos = **20260907**, es decir un solo día. Se pueden cambiar con --desde/--hasta.

Devuelve la respuesta TAL CUAL viene (sobre completo {Status, Code, Value}), sin
filtrar ni interpretar. El cliente del repo se usa SOLO para el token.

⚠️ Según el manual, este método devuelve "todas las OPERACIONES del mercado de
FCI" — o sea trades (suscripciones/rescates), NO el stock/tenencia a una fecha.
Este diag solo muestra lo que trae; no asume que sea la tenencia.

READ-ONLY (un GET).

Uso (desde la raíz del repo):
    python -m scripts.diag_a3_fci_trades_raw
    python -m scripts.diag_a3_fci_trades_raw --desde 20260901 --hasta 20260907
    python -m scripts.diag_a3_fci_trades_raw --guardar fci_trades.json
"""
from __future__ import annotations

import argparse
import json
import sys

import requests

from core import postrade

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PATH = "PosTrade/TradeCaptureReportFCI"


def main() -> None:
    ap = argparse.ArgumentParser(description="TradeCaptureReportFCI crudo (READ-ONLY).")
    ap.add_argument("--desde", default="20260907", help="DateFrom AAAAMMDD (default 20260907)")
    ap.add_argument("--hasta", default="20260907", help="DateTo AAAAMMDD (default 20260907)")
    ap.add_argument("--guardar", help="ruta de archivo para volcar el JSON crudo")
    args = ap.parse_args()

    url = f"{postrade._base()}/{PATH}"
    params = {"DateFrom": args.desde, "DateTo": args.hasta}

    print(f"GET {url}")
    print(f"    params = {params}\n")

    try:
        headers = postrade.auth_headers()
    except postrade.PostradeError as e:
        print(f"✗ No se pudo obtener token: {e}")
        raise SystemExit(2) from None

    headers = {**headers, "Accept": "application/json"}
    r = requests.get(url, params=params, headers=headers, timeout=120)

    print(f"HTTP {r.status_code}")
    try:
        data = r.json()
    except ValueError:
        print("(respuesta no es JSON) — texto crudo:")
        print(r.text[:4000])
        return

    crudo = json.dumps(data, ensure_ascii=False, indent=2)

    if args.guardar:
        with open(args.guardar, "w", encoding="utf-8") as fh:
            fh.write(crudo)
        valor = data.get("Value") if isinstance(data, dict) else None
        n = len(valor) if isinstance(valor, list) else "?"
        print(f"Status={data.get('Status')!r} Code={data.get('Code')!r}  Value: {n} filas")
        print(f"JSON crudo completo guardado en: {args.guardar}")
    else:
        print(crudo)


if __name__ == "__main__":
    main()
