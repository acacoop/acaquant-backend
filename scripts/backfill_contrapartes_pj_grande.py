"""Backfill: nivel_3 = "PJ GRANDE" para las Comitentes activas cuyo id_cuenta
está en CashFlow.Contrapartes (FCI / sociedades gerentes / etc.).

Regla de negocio (ver api/services/segmentacion.py): si el id_cuenta es contraparte
→ SIEMPRE PJ GRANDE, sin importar cupo/UVA. La lógica ya quedó en el código
(`es_contraparte`); este script aplica la regla a los docs YA existentes. SOLO
toca esas cuentas — no el resto (no las pisa con null como haría el motor completo
sin cupo cargado).

Server-side update_many (sin cursor), idempotente. Dry-run por default.

Uso (desde la raíz del repo, en el Droplet):
    python -m scripts.backfill_contrapartes_pj_grande            # dry-run
    python -m scripts.backfill_contrapartes_pj_grande --apply    # aplica
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime

from api.services.segmentacion import cargar_ids_contrapartes
from core.mongo import get_mongo_client

_LABEL = "PJ GRANDE"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="ejecutar (default: dry-run)")
    args = ap.parse_args()

    client = get_mongo_client()
    col = client["Clientes"]["Comitentes"]
    ids = list(cargar_ids_contrapartes(client["CashFlow"]))
    base = {"estado": "Activa", "id_cuenta": {"$in": ids}}
    total = col.count_documents(base)
    ya_ok = col.count_documents({**base, "nivel_3": _LABEL})
    a_cambiar = col.count_documents({**base, "nivel_3": {"$ne": _LABEL}})
    print(f"Contrapartes (ids): {len(ids)} | Comitentes activas que matchean: {total}")
    print(f"  ya en '{_LABEL}': {ya_ok} | a cambiar: {a_cambiar}")

    if not args.apply:
        print("\n(dry-run) — pasar --apply para escribir.")
        return

    res = col.update_many(
        {**base, "nivel_3": {"$ne": _LABEL}},
        {"$set": {"nivel_3": _LABEL, "actualizado_at": datetime.now(UTC),
                  "actualizado_por": "backfill:contrapartes_pj_grande"}},
    )
    print(f"\nOK. Actualizadas: {res.modified_count}")


if __name__ == "__main__":
    main()
