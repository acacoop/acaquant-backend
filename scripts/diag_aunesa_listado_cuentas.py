"""Diag read-only de Aunesa GET /api/cuentas/listadoCuentas.

Verifica el shape REAL del listado de cuentas comitentes para diseñar el
master `Clientes.Comitentes` del Tablero de Control Comercial
(docs/TABLERO_COMERCIAL.md). NO escribe nada — solo auth + GET.

El endpoint YA se usa en jobs/aum.py (obtener_cuentas), pero ahí solo se
leen id/denominacion. Acá traemos el doc COMPLETO para ver:
  - valores reales de tipo / estado (el doc dice alta/prealta/baja, pero
    aum.py filtra estado=="Activa" → confirmar cuál es).
  - campos de segmentación (cartera, categoria, clase, tipoCliente, etc).
  - administrador.operador.email (clave: operador ↔ usuario de la página).

Uso:
    python -m scripts.diag_aunesa_listado_cuentas
    python -m scripts.diag_aunesa_listado_cuentas --tipo Comitente --samples 3
    python -m scripts.diag_aunesa_listado_cuentas --cuenta 805   # campos exactos de una cuenta
"""
from __future__ import annotations

import argparse
import json
from collections import Counter

import requests

import config

AUTH_URL = "https://aca.aunesa.com/Irmo/api/login"
LISTADO_URL = "https://aca.aunesa.com/Irmo/api/cuentas/listadoCuentas"


def _auth() -> dict[str, str]:
    r = requests.post(
        AUTH_URL,
        json={
            "clientId": config.AUNESA_CLIENT_ID,
            "username": config.AUNESA_USERNAME,
            "password": config.AUNESA_PASSWORD,
        },
        headers={"Content-Type": "application/json"},
        timeout=15,
    )
    r.raise_for_status()
    return {"Content-Type": "application/json", "Authorization": f"Bearer {r.json().get('token')}"}


def _get(d: dict, path: str):
    cur = d
    for k in path.split("."):
        cur = cur.get(k) if isinstance(cur, dict) else None
    return cur


def _dist(data: list[dict], path: str, top: int = 12) -> None:
    c: Counter = Counter()
    for d in data:
        v = _get(d, path)
        c[v if v not in (None, "") else "—"] += 1
    print(f"── {path} ──")
    for v, n in c.most_common(top):
        print(f"   {n:>5}  {v}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tipo", default=None, help="tipoCuenta (ej. Comitente)")
    ap.add_argument("--estado", default=None, help="estado (alta/prealta/baja)")
    ap.add_argument("--cuenta", action="append", default=None,
                    help="idCuenta específica (repetible). Ej: --cuenta 805")
    ap.add_argument("--samples", type=int, default=2, help="docs completos a imprimir")
    args = ap.parse_args()

    headers = _auth()
    params: dict[str, object] = {}
    if args.tipo:
        params["tipoCuenta"] = args.tipo
    if args.estado:
        params["estado"] = args.estado
    if args.cuenta:
        # requests serializa la lista como idCuenta=805&idCuenta=... (formato del doc)
        params["idCuenta"] = args.cuenta

    r = requests.get(LISTADO_URL, headers=headers, params=params, timeout=120)
    print(f"GET listadoCuentas {params} → HTTP {r.status_code}")
    if r.status_code != 200:
        print(r.text[:2000])
        return

    data = r.json()
    if isinstance(data, dict):
        data = data.get("cuentas") or data.get("data") or [data]
    print(f"Total cuentas: {len(data)}\n")
    if not data:
        return

    for path in (
        "tipo", "estado", "tipoTitular", "cartera", "categoria", "clase",
        "disposicionesGenerales.tipoCliente",
        "disposicionesGenerales.perfilInversion",
        "disposicionesGenerales.horizonteInversion",
    ):
        _dist(data, path)

    # Operador — clave para mapear operador ↔ usuario de la página (Manager.Users).
    con_op = sum(1 for d in data if _get(d, "administrador.operador.email"))
    print(f"Cuentas con administrador.operador.email poblado: {con_op}/{len(data)}\n")
    op_emails: Counter = Counter(
        (_get(d, "administrador.operador.email") or "—") for d in data
    )
    print("Operadores (email → # cuentas):")
    for em, n in op_emails.most_common(20):
        print(f"   {n:>5}  {em}")
    print()

    # Si pediste cuenta(s) puntual(es), dumpeamos TODO sin truncar (es el
    # objetivo: ver los campos exactos). Si no, una muestra acotada.
    print("=" * 72)
    if args.cuenta:
        print(f"CAMPOS EXACTOS de cuenta(s) {args.cuenta}:")
        for d in data:
            print("-" * 72)
            print(json.dumps(d, ensure_ascii=False, indent=2))
    else:
        print(f"MUESTRAS COMPLETAS (primeras {args.samples}):")
        for d in data[: args.samples]:
            print("-" * 72)
            print(json.dumps(d, ensure_ascii=False, indent=2)[:3500])


if __name__ == "__main__":
    main()
