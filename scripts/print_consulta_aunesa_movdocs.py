"""scripts/print_consulta_aunesa_movdocs.py — imprime la CONSULTA EXACTA que Tesorería
le hace hoy a Aunesa, y los CAMPOS REALES que devuelve (para revisar con el equipo).

Reusa la MISMA lógica de producción (`api/services/tesoreria`) → el request es literal el
que se manda. Los campos NO están hardcodeados: se DERIVAN de la respuesta real (unión de
todas las claves + fill-rate). Por eso ejecuta la llamada por defecto; con `--no-run` solo
muestra el request sin pegarle a Aunesa.

Uso:
  python -m scripts.print_consulta_aunesa_movdocs                 # request + campos reales (hoy)
  python -m scripts.print_consulta_aunesa_movdocs --fecha 2026-07-03
  python -m scripts.print_consulta_aunesa_movdocs --no-run        # solo el request
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

import requests

from api.services.tesoreria import _ENDPOINT, _fechas
from core import aunesa


def _campos_reales(rows: list[dict]) -> list[tuple[str, int]]:
    """Unión de TODAS las claves de la respuesta (persona.* aplanada) + cuántas filas traen
    valor real en cada una. Derivado de los datos, sin lista hardcodeada."""
    llenos: dict[str, int] = {}
    presentes: set[str] = set()
    for r in rows:
        for k, v in r.items():
            if k == "persona":
                presentes.add("persona")
                for pk, pv in (v or {}).items():
                    key = f"persona.{pk}"
                    presentes.add(key)
                    if pv not in (None, "", [], {}):
                        llenos[key] = llenos.get(key, 0) + 1
                if v:
                    llenos["persona"] = llenos.get("persona", 0) + 1
            else:
                presentes.add(k)
                if v not in (None, "", [], {}):
                    llenos[k] = llenos.get(k, 0) + 1
    return [(k, llenos.get(k, 0)) for k in sorted(presentes)]


def main() -> int:
    p = argparse.ArgumentParser(description="Imprime la consulta de Tesorería a Aunesa.")
    p.add_argument("--fecha", default=None, help="ISO YYYY-MM-DD (default hoy ART)")
    p.add_argument("--estado", default="Procesado")
    p.add_argument("--no-run", action="store_true", help="no ejecutar, solo mostrar el request")
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
    print("=" * 78)

    if args.no_run:
        print("\n(--no-run: no se ejecutó; no puedo mostrar los campos REALES sin correrlo)")
        return 0

    print("\nEjecutando en vivo para leer los CAMPOS REALES…")
    resp = aunesa.get(_ENDPOINT, params)
    print(f"  status = {resp.status_code}")
    if resp.status_code != 200:
        print(f"  body = {resp.text[:300]}")
        return 1
    body: Any = resp.json()
    rows = body if isinstance(body, list) else []
    print(f"  filas devueltas = {len(rows)}")
    if not rows:
        print("  (sin filas — probá otra fecha/estado para ver los campos)")
        return 0
    print(f"\n  CAMPOS REALES que devuelve cada movimiento (derivados de las {len(rows)} filas,")
    print("  con cuántas las traen con valor — NO es una lista escrita a mano):")
    for k, n in _campos_reales(rows):
        print(f"      {k:26} {n:5}/{len(rows)}")
    print("\n  (si NO ves un campo de 'cuenta operativa'/cuenta bancaria de ACA acá, no lo trae)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
