"""Backfill: cupo en 0 (transaccional + usado) para los Fondo Común de Inversión.

Regla: un FCI no tiene cupo de fondeo real → `cupo.transaccional_ars = 0`,
`cupo.usado_ars = 0`, `utilizacion_pct = 0`. Target = `tipo_cliente == "Fondo
Común de Inversión"`. SOLO toca esas cuentas (no el resto).

Server-side update_many (sin cursor), idempotente. Dry-run por default.

Uso (desde la raíz del repo, en el Droplet):
    python -m scripts.backfill_cupo_cero_fci            # dry-run (no escribe)
    python -m scripts.backfill_cupo_cero_fci --apply    # aplica
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime

from core.mongo import get_mongo_client

_TIPO_FCI = "Fondo Común de Inversión"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="ejecutar (default: dry-run)")
    args = ap.parse_args()

    col = get_mongo_client()["Clientes"]["Comitentes"]
    base = {"tipo_cliente": _TIPO_FCI}
    total = col.count_documents(base)
    ya_cero = col.count_documents({**base, "cupo.transaccional_ars": 0, "cupo.usado_ars": 0})
    con_cupo_pos = col.count_documents({**base, "cupo.transaccional_ars": {"$gt": 0}})
    print(f"FCI (tipo_cliente='{_TIPO_FCI}'): {total}")
    print(f"  ya en cupo 0/0: {ya_cero} | con cupo transaccional > 0 (se pisaría a 0): {con_cupo_pos}")

    if not args.apply:
        print("\n(dry-run) — pasar --apply para escribir. SOLO toca los FCI.")
        return

    res = col.update_many(base, {"$set": {
        "cupo.transaccional_ars": 0.0,
        "cupo.usado_ars": 0.0,
        "cupo.utilizacion_pct": 0.0,
        "cupo.cargado_en": datetime.now(UTC),
        "cupo.fuente": "backfill:cupo_cero_fci",
    }})
    print(f"\nOK. Matched: {res.matched_count} | Modificadas: {res.modified_count}")


if __name__ == "__main__":
    main()
