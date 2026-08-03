"""scripts/diag_aunesa_cuentas.py — consulta CRUDA a Aunesa `listadoCuentas` (SIN filtros)
y desarma el universo real de cuentas antes de que el sync le aplique cualquier criterio.

READ-ONLY (no escribe nada, ni en Aunesa ni en la DB). Responde:
  · cuántas cuentas devuelve Aunesa en total
  · el corte por `tipo` (Comitente / Propia / ...) y por `estado` (Activa / ...)
  · el cruce tipo × estado (la matriz completa)
  · cuántas de esas ya están en SQL `clientes.comitentes` y cuáles NO (el delta real)

`jobs/sync_comitentes.py` hoy pide `tipoCuenta=Comitente` y además filtra
`estado == 'Activa'` en memoria → todo lo que no cumpla las dos cosas nunca entra
a la base y por lo tanto no existe para el Informe.

Uso:
    python -m scripts.diag_aunesa_cuentas
    python -m scripts.diag_aunesa_cuentas --por-tipo           # una llamada por cada tipo del selector
    python -m scripts.diag_aunesa_cuentas --tipo Comitente     # acota a un tipo
    python -m scripts.diag_aunesa_cuentas --crudo              # imprime 1 registro completo (OJO: PII)
"""
from __future__ import annotations

import argparse
import json
from collections import Counter

import requests

import config
from core.postgres import get_pool

AUTH_URL = "https://aca.aunesa.com/Irmo/api/login"
LISTADO_URL = "https://aca.aunesa.com/Irmo/api/cuentas/listadoCuentas"

# Los 8 tipos del selector de Aunesa. Se usan en --por-tipo: si el endpoint exige
# `tipoCuenta` (o ignora la ausencia del param), una llamada por tipo garantiza el universo.
TIPOS = ("Agente", "Comitente", "Contabilidad", "Ente", "Intermediación",
         "Mercado", "Propia", "Proveedor")


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


def _listado(headers: dict, params: dict) -> list[dict]:
    r = requests.get(LISTADO_URL, headers=headers, params=params, timeout=180)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        data = data.get("cuentas") or data.get("data") or [data]
    return [c for c in data if isinstance(c, dict)]


def _titulo(t: str) -> None:
    print(f"\n{'─' * 78}\n{t}\n{'─' * 78}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Consulta cruda a Aunesa listadoCuentas.")
    ap.add_argument("--tipo", help="pasa tipoCuenta=<X> al endpoint (default: sin filtro).")
    ap.add_argument("--por-tipo", action="store_true",
                    help="una llamada por cada tipo conocido y une los resultados.")
    ap.add_argument("--crudo", action="store_true", help="imprime 1 registro completo (contiene PII).")
    args = ap.parse_args()

    headers = _auth()
    if args.por_tipo:
        params = {"tipoCuenta": "(barrido por tipo)"}
        vistos: dict[str, dict] = {}
        print("Barrido por tipo:")
        for t in TIPOS:
            try:
                lote = _listado(headers, {"tipoCuenta": t})
            except requests.HTTPError as e:
                print(f"  {t:<16} ERROR {e.response.status_code}")
                continue
            print(f"  {t:<16} {len(lote):>6}")
            for c in lote:
                if c.get("id") is not None:
                    vistos[str(c["id"])] = c
        cuentas = list(vistos.values())
    else:
        params = {"tipoCuenta": args.tipo} if args.tipo else {}
        cuentas = _listado(headers, params)

    _titulo("1) RESPUESTA CRUDA")
    print(f"params enviados : {params or '(ninguno — sin filtro)'}")
    print(f"cuentas recibidas: {len(cuentas)}")
    if cuentas:
        print(f"campos del item : {sorted(cuentas[0].keys())}")

    _titulo("2) CORTE POR `tipo`")
    for tipo, n in Counter((c.get("tipo") or "(vacío)") for c in cuentas).most_common():
        print(f"{tipo:<28} {n:>6}")

    _titulo("3) CORTE POR `estado`")
    for estado, n in Counter((c.get("estado") or "(vacío)") for c in cuentas).most_common():
        print(f"{estado:<28} {n:>6}")

    _titulo("4) MATRIZ tipo × estado (lo que el sync deja entrar y lo que descarta)")
    matriz = Counter(((c.get("tipo") or "(vacío)"), (c.get("estado") or "(vacío)")) for c in cuentas)
    for (tipo, estado), n in sorted(matriz.items(), key=lambda kv: -kv[1]):
        entra = "  ✅ entra al sync" if (tipo == "Comitente" and estado == "Activa") else "  ❌ descartada"
        print(f"{tipo:<20} {estado:<20} {n:>6}{entra}")

    _titulo("5) DELTA CONTRA SQL `clientes.comitentes`")
    ids_aunesa = {str(c["id"]) for c in cuentas if c.get("id") is not None}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id_cuenta FROM clientes.comitentes")
        ids_sql = {r[0] for r in cur.fetchall()}
    faltan = ids_aunesa - ids_sql
    sobran = ids_sql - ids_aunesa
    print(f"ids en la respuesta de Aunesa      : {len(ids_aunesa)}")
    print(f"ids en SQL clientes.comitentes     : {len(ids_sql)}")
    print(f"→ en Aunesa y NO en SQL (faltan)   : {len(faltan)}")
    print(f"→ en SQL y NO en esta respuesta    : {len(sobran)}")

    if faltan:
        print("\nLas que faltan, por qué las descarta el sync (tipo × estado):")
        det = Counter(
            ((c.get("tipo") or "(vacío)"), (c.get("estado") or "(vacío)"))
            for c in cuentas if str(c.get("id")) in faltan
        )
        for (tipo, estado), n in sorted(det.items(), key=lambda kv: -kv[1]):
            print(f"  {tipo:<20} {estado:<20} {n:>6}")
        print("\n  Muestra de 15 ids faltantes: " + ", ".join(sorted(faltan)[:15]))

    if args.crudo and cuentas:
        _titulo("6) REGISTRO CRUDO (1) — contiene datos personales")
        print(json.dumps(cuentas[0], ensure_ascii=False, indent=2)[:4000])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
