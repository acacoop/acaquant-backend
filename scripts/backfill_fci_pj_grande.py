"""Backfill: nivel_3 = "PJ GRANDE" para los Fondo Común de Inversión.

Regla de negocio (ver api/services/segmentacion.py): un FCI es SIEMPRE PJ GRANDE.
Target = `tipo_cliente == "Fondo Común de Inversión"` (señal fresca del sync de
Aunesa — más confiable que CashFlow.Contrapartes, que estaba desactualizado y
captaba pocas). SOLO toca esas cuentas.

El dry-run LISTA las cuentas que matchean (para revisarlas antes de escribir).
Server-side update_many, idempotente. Dry-run por default.

Uso (desde la raíz del repo, en el Droplet):
    python -m scripts.backfill_fci_pj_grande            # dry-run: lista + cuenta
    python -m scripts.backfill_fci_pj_grande --apply    # aplica
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime

from core.mongo import get_mongo_client

_TIPO_FCI = "Fondo Común de Inversión"
_LABEL = "PJ GRANDE"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="ejecutar (default: dry-run)")
    args = ap.parse_args()

    col = get_mongo_client()["Clientes"]["Comitentes"]
    base = {"tipo_cliente": _TIPO_FCI}

    rows = list(col.find(
        base, {"_id": 0, "id_cuenta": 1, "denominacion": 1, "estado": 1, "nivel_3": 1}
    ).sort("id_cuenta", 1))
    total = len(rows)
    estados = Counter(r.get("estado") or "(sin estado)" for r in rows)
    ya_ok = sum(1 for r in rows if r.get("nivel_3") == _LABEL)
    a_cambiar = total - ya_ok
    print(f"FCI (tipo_cliente='{_TIPO_FCI}'): {total}")
    print(f"  por estado: {dict(estados)}")
    print(f"  ya en '{_LABEL}': {ya_ok} | a cambiar: {a_cambiar}\n")

    print("Cuentas (id | estado | nivel_3 actual | denominación):")
    for r in rows:
        print(f"  {r.get('id_cuenta')!s:<8} {r.get('estado') or '-'!s:<10} "
              f"{r.get('nivel_3') or '—'!s:<16} {str(r.get('denominacion'))[:40]}")

    if not args.apply:
        print("\n(dry-run) — pasar --apply para escribir. SOLO toca los FCI.")
        return

    res = col.update_many(
        {**base, "nivel_3": {"$ne": _LABEL}},
        {"$set": {"nivel_3": _LABEL, "actualizado_at": datetime.now(UTC),
                  "actualizado_por": "backfill:fci_pj_grande"}},
    )
    print(f"\nOK. Actualizadas: {res.modified_count}")


if __name__ == "__main__":
    main()
