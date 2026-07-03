"""scripts/print_consulta_aunesa_movdocs.py — imprime la CONSULTA EXACTA que Tesorería
le hace hoy a Aunesa (para revisar con el equipo).

Reusa la MISMA lógica que corre en producción (`api/services/tesoreria`) → lo que ves acá
es literal lo que se manda. Por defecto NO ejecuta (solo muestra el request); con
`--ejecutar` le pega en vivo y muestra el status + cuántas filas trae.

Uso:
  python -m scripts.print_consulta_aunesa_movdocs                 # muestra el request (hoy)
  python -m scripts.print_consulta_aunesa_movdocs --fecha 2026-07-03
  python -m scripts.print_consulta_aunesa_movdocs --ejecutar      # + lo corre en vivo
"""
from __future__ import annotations

import argparse
import sys

import requests

from api.services.tesoreria import _ENDPOINT, _fechas
from core import aunesa

# Campos que DEVUELVE la response (medidos por el discovery, no de la doc).
CAMPOS_RESPONSE = [
    "id", "idExterno", "solicitud (Depósito=ingreso / Extracción=egreso)", "tipoDocSoli",
    "persona {tipoDocumento, documento, tipoPersona, cuit, nombreCompleto}",
    "cuenta (comitente, NO cuenta bancaria)", "fecha", "estado", "cbuCVU (contraparte)",
    "banco (contraparte, viene null ~96%)", "unidad (ARS/USD)", "monto (siempre positivo)",
]


def main() -> int:
    p = argparse.ArgumentParser(description="Imprime la consulta de Tesorería a Aunesa.")
    p.add_argument("--fecha", default=None, help="ISO YYYY-MM-DD (default hoy ART)")
    p.add_argument("--estado", default="Procesado")
    p.add_argument("--ejecutar", action="store_true", help="además, correrlo en vivo")
    args = p.parse_args()

    ddmmyyyy, ddmmyyyy_hasta, _ = _fechas(args.fecha)
    params: dict[str, str] = {"liquidacionDesde": ddmmyyyy, "liquidacionHasta": ddmmyyyy_hasta}
    if args.estado:
        params["estados"] = args.estado

    url_base = f"{aunesa.BASE_URL}/{_ENDPOINT}"
    url_full = requests.Request("GET", url_base, params=params).prepare().url

    print("=" * 78)
    print("CONSULTA A AUNESA — Tesorería · Movimientos y Documentos Solicitados")
    print("=" * 78)
    print("  Sistema     : Cuentas (Aunesa / Irmo)")
    print("  Método      : GET")
    print(f"  Endpoint    : {_ENDPOINT}")
    print(f"  URL base    : {url_base}")
    print("\n  Parámetros enviados:")
    for k, v in params.items():
        print(f"      {k:18} = {v}")
    print("\n  NOTA: liquidacionHasta = día + 1 a propósito — Aunesa EXIGE desde < hasta")
    print("        (un solo día con desde==hasta tira 400). Después filtramos al día objetivo.")
    print(f"\n  URL completa (lo que viaja):\n      {url_full}")
    print("\n  Autenticación:")
    print(f"      1) POST {aunesa.BASE_URL}/login  (clientId/username/password)  → token")
    print("      2) Header:  Authorization: Bearer <token>   +   Content-Type: application/json")
    print("\n  Campos que DEVUELVE cada movimiento (medido, no de la doc):")
    for c in CAMPOS_RESPONSE:
        print(f"      · {c}")
    print("\n  ⚠ NO devuelve la 'cuenta operativa' (cuenta bancaria de ACA). El único 'banco'")
    print("     que trae es el de la CONTRAPARTE (cliente) y viene null en ~96% de las filas.")
    print("=" * 78)

    if args.ejecutar:
        print("\nEjecutando en vivo…")
        resp = aunesa.get(_ENDPOINT, params)
        print(f"  status = {resp.status_code}")
        if resp.status_code == 200:
            body = resp.json()
            n = len(body) if isinstance(body, list) else "?"
            print(f"  filas devueltas = {n}")
        else:
            print(f"  body = {resp.text[:300]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
