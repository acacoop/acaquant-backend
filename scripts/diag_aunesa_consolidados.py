"""Diag read-only de Aunesa GET /operaciones/consolidadosGenerales.

Objetivo: ver TODOS los campos crudos que devuelve el endpoint de operaciones
(boletos), para chequear si trae aranceles/comisiones. El job de ingesta
(api/services/aunesa_negocio.py) parsea y DESCARTA campos crudos, así que acá
pegamos directo y dumpeamos sin tocar nada. NO escribe en Mongo.

Forza una respuesta de una cuenta puntual (ej. 805) sobre un rango de fechas
(las cuentas no operan todos los días → un rango ancho aumenta la chance de
encontrar boletos). Filtra client-side por el `[id]` en el campo `cuenta`.

Uso (en el Droplet, desde la raíz):
    python -m scripts.diag_aunesa_consolidados --cuenta 805
    python -m scripts.diag_aunesa_consolidados --cuenta 805 --desde 2026-01-01 --hasta 2026-05-26
    python -m scripts.diag_aunesa_consolidados --desde 2026-05-01 --hasta 2026-05-26   # sin filtro de cuenta
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, timedelta

import requests

import config

AUTH_URL = "https://aca.aunesa.com/Irmo/api/login"
OPS_URL = "https://aca.aunesa.com/Irmo/api/operaciones/consolidadosGenerales"

# Claves que delatan aranceles / costos (lo que buscamos).
_ARANCEL_HINTS = ("aran", "comis", "gasto", "derecho", "iva", "tarifa", "costo", "fee", "cargo")


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


def _to_ddmmyyyy(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d/%m/%Y")


def main() -> int:
    ap = argparse.ArgumentParser(description="Dump crudo de consolidadosGenerales (read-only).")
    ap.add_argument("--cuenta", action="append", default=None,
                    help="filtra por id de cuenta en el campo `cuenta` (ej. 805). Repetible.")
    ap.add_argument("--desde", default=None, help="YYYY-MM-DD (default: hace 90 días).")
    ap.add_argument("--hasta", default=None, help="YYYY-MM-DD (default: hoy).")
    ap.add_argument("--tipos", default="Comitente", help="tiposCuenta (default Comitente).")
    ap.add_argument("--samples", type=int, default=3, help="items completos a dumpear.")
    args = ap.parse_args()

    hasta = args.hasta or date.today().isoformat()
    desde = args.desde or (date.fromisoformat(hasta) - timedelta(days=90)).isoformat()

    headers = _auth()
    params = {
        "tiposCuenta": args.tipos,
        "concertacionDesde": _to_ddmmyyyy(desde),
        "concertacionHasta": _to_ddmmyyyy(hasta),
    }
    print(f"GET consolidadosGenerales {params}")
    r = requests.get(OPS_URL, params=params, headers=headers, timeout=180)
    print(f"→ HTTP {r.status_code}")
    if r.status_code != 200:
        print(r.text[:1500])
        return 1

    body = (r.text or "").strip()
    data = r.json() if body else []
    if not isinstance(data, list):
        print(f"shape inesperada: {type(data).__name__}\n{str(data)[:1000]}")
        return 1
    print(f"Total líneas en el rango {desde}..{hasta}: {len(data)}\n")
    if not data:
        print("Rango sin operaciones. Probá un rango más ancho o tipos='Comitentes y propias'.")
        return 0

    # 1) UNIÓN DE TODAS LAS CLAVES posibles (sobre todo el rango, sin filtrar).
    todas_claves: dict[str, str] = {}
    for it in data:
        if isinstance(it, dict):
            for k, v in it.items():
                todas_claves.setdefault(k, type(v).__name__)
    print("═" * 72)
    print(f"TODAS LAS CLAVES POSIBLES ({len(todas_claves)}):")
    for k in sorted(todas_claves):
        flag = "  ⬅️ ¿ARANCEL?" if any(h in k.lower() for h in _ARANCEL_HINTS) else ""
        print(f"   {k:<28} ({todas_claves[k]}){flag}")

    aranceles = [k for k in todas_claves if any(h in k.lower() for h in _ARANCEL_HINTS)]
    print("\n" + "═" * 72)
    if aranceles:
        print(f"✅ Posibles campos de arancel/comisión: {aranceles}")
    else:
        print("❌ NO hay ninguna clave que parezca arancel/comisión en este endpoint.")
        print("   (El arancel quizás viaja en otro endpoint de Aunesa, o embebido en `informacion`.)")

    # 2) Items de la(s) cuenta(s) pedida(s) — dump completo.
    if args.cuenta:
        ids = set(args.cuenta)
        pat = re.compile(r"^\[(\d+)\]")
        items = [it for it in data
                 if isinstance(it, dict)
                 and (m := pat.match(it.get("cuenta") or "")) and m.group(1) in ids]
        print("\n" + "═" * 72)
        print(f"ITEMS DE CUENTA(S) {sorted(ids)}: {len(items)} líneas")
        for it in items[: args.samples]:
            print("-" * 72)
            print(json.dumps(it, ensure_ascii=False, indent=2))
        if not items:
            print(f"   (La(s) cuenta(s) {sorted(ids)} no operó en este rango. Dumpeo muestras "
                  "generales para que veas los valores reales:)")
            for it in data[: args.samples]:
                print("-" * 72)
                print(json.dumps(it, ensure_ascii=False, indent=2))
    else:
        print("\n" + "═" * 72)
        print(f"MUESTRAS (primeras {args.samples}):")
        for it in data[: args.samples]:
            print("-" * 72)
            print(json.dumps(it, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
