"""scripts/diag_a3_position_raw.py — PositionReport CRUDO, sin interpretar nada.

Consulta MÍNIMA basada 100% en el manual de la API (pág. 81-83):

    GET /PosTrade/PositionReport?clearingBusinessDate=YYYYMMDD&viewDetails=false

Único parámetro obligatorio: `clearingBusinessDate`. Acá se fija por defecto en
**20260907** (7-sep-2026). Se puede cambiar con --fecha.

Devuelve la respuesta TAL CUAL viene (el sobre completo {Status, Code, Value}),
sin filtrar, sin aplanar, sin quedarse con unos campos y descartar otros. La idea
es ver con los propios ojos qué trae la API. Usa el cliente del repo SOLO para el
token de autenticación; la llamada y el volcado son directos.

READ-ONLY (un GET).

Uso (desde la raíz del repo):
    python -m scripts.diag_a3_position_raw
    python -m scripts.diag_a3_position_raw --fecha 20260907
    python -m scripts.diag_a3_position_raw --guardar posicion_20260907.json
"""
from __future__ import annotations

import argparse
import json
import sys

import requests

from core import postrade

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PATH = "PosTrade/PositionReport"


def main() -> None:
    ap = argparse.ArgumentParser(description="PositionReport crudo (READ-ONLY).")
    ap.add_argument("--fecha", default="20260907", help="AAAAMMDD (default 20260907)")
    ap.add_argument("--view-details", default="false", choices=["true", "false"])
    ap.add_argument("--guardar", help="ruta de archivo para volcar el JSON crudo")
    args = ap.parse_args()

    url = f"{postrade._base()}/{PATH}"
    params = {"clearingBusinessDate": args.fecha, "viewDetails": args.view_details}

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
        # Además un resumen de una línea para no volcar 100k de texto a la consola.
        valor = data.get("Value") if isinstance(data, dict) else None
        n = len(valor) if isinstance(valor, list) else "?"
        print(f"Status={data.get('Status')!r} Code={data.get('Code')!r}  Value: {n} filas")
        print(f"JSON crudo completo guardado en: {args.guardar}")
    else:
        print(crudo)


if __name__ == "__main__":
    main()
