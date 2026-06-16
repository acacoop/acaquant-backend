"""scripts/drop_mongo_negociomovimientos.py — dropea Mongo `CashFlow.NegocioMovimientos`,
ya deprecada: la fuente única es SQL `operaciones.negocio_movimientos` (la escribe
directo jobs/negocio_movimientos.py; aranceles, fci_bilateral, pnl totales, valuaciones,
auditor de boletos y sin_operador/back_office leen SQL; sync_negocio eliminado).

    python -m scripts.drop_mongo_negociomovimientos            # PREVIEW (cuenta, no borra)
    python -m scripts.drop_mongo_negociomovimientos --commit   # DROPEA

Irreversible. Antes de correr con --commit, confirmá que en prod están activos los
flags SQL (NEGOCIO_SQL, COMERCIAL_SQL, PNL_SQL, VALUACIONES_SQL) y que ya corrió
el writer SQL al menos una vez. El dato vive en operaciones.negocio_movimientos.
"""
from __future__ import annotations

import sys

from core.mongo import get_mongo_client


def main() -> None:
    commit = "--commit" in sys.argv
    db = get_mongo_client()["CashFlow"]
    existe = "NegocioMovimientos" in db.list_collection_names()
    n = db["NegocioMovimientos"].estimated_document_count() if existe else 0
    print(f"\n=== Mongo CashFlow.NegocioMovimientos ===\n"
          f"   {'existe' if existe else 'NO existe'}  ·  docs≈{n}")
    if not commit:
        print("\n[PREVIEW] no se borró nada. Repetí con --commit.\n")
        return
    if existe:
        db.drop_collection("NegocioMovimientos")
        print("   ✓ dropeada CashFlow.NegocioMovimientos")
    print("\n[COMMIT] listo. Chau Mongo NegocioMovimientos.\n")


if __name__ == "__main__":
    main()
