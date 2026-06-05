"""Backfill: agrega tipo_doc / nro_doc a Clientes.Comitentes desde Aunesa.

El cruce del Control Automático necesita el documento fiscal de cada cuenta. El
campo `titular` de Aunesa lo tiene ('[DNI 93698623] NOMBRE') pero no estaba
persistido. `sync_comitentes` ya lo guarda para cuentas nuevas/re-syncs; este
script lo backfillea para las ~1881 existentes en una pasada (volumen chico,
solo agrega 2 campos por id_cuenta → idempotente, REGLA #4 OK).

Uso:
    python -m scripts.enrich_doc_comitentes            # aplica
    python -m scripts.enrich_doc_comitentes --dry-run  # solo cuenta, no escribe
"""
from __future__ import annotations

import argparse

import requests
from pymongo import UpdateOne

import config
from core.doc_fiscal import parse_titular
from core.mongo import get_mongo_client

AUTH_URL = "https://aca.aunesa.com/Irmo/api/login"
LISTADO_URL = "https://aca.aunesa.com/Irmo/api/cuentas/listadoCuentas"


def _auth() -> dict:
    r = requests.post(AUTH_URL, json={
        "clientId": config.AUNESA_CLIENT_ID,
        "username": config.AUNESA_USERNAME,
        "password": config.AUNESA_PASSWORD,
    }, timeout=60)
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['token']}", "Content-Type": "application/json"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="No escribe; solo cuenta.")
    args = ap.parse_args()

    r = requests.get(LISTADO_URL, headers=_auth(), params={"tipoCuenta": "Comitente"}, timeout=180)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        data = data.get("cuentas") or data.get("data") or [data]
    print(f"cuentas de Aunesa: {len(data)}")

    ops: list[UpdateOne] = []
    sin_doc = 0
    for c in data:
        idc = str(c.get("id")) if c.get("id") is not None else None
        if not idc:
            continue
        tipo_doc, nro_doc = parse_titular(c.get("titular"))
        if not nro_doc:
            sin_doc += 1
            continue
        ops.append(UpdateOne({"id_cuenta": idc},
                             {"$set": {"tipo_doc": tipo_doc, "nro_doc": nro_doc}}))

    print(f"con documento: {len(ops)} · sin documento parseable: {sin_doc}")
    if args.dry_run:
        print("[dry-run] no se escribió nada.")
        return 0

    col = get_mongo_client()["Clientes"]["Comitentes"]
    col.create_index("nro_doc")
    res = col.bulk_write(ops, ordered=False)
    print(f"✅ Backfill: {res.modified_count} cuentas actualizadas (índice nro_doc creado).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
