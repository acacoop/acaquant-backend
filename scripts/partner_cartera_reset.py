"""partner_cartera_reset.py — vacía la colección ACAPortfolio.Cartera.

Borra TODOS los docs de ACAPortfolio.Cartera para empezar de cero. Pensado
para correr una vez antes de repoblar con el job nuevo `jobs.partner_export`
(que pega directo a Aunesa, sin pasar por Valuaciones.AuM).

Dry-run por default: muestra cuántos docs hay y no toca nada. Pasá --apply
para borrar de verdad.

Uso:
    python -m scripts.partner_cartera_reset            # ver cuántos hay
    python -m scripts.partner_cartera_reset --apply    # vaciar la colección
"""
from __future__ import annotations

import sys

from core.mongo import get_mongo_client

_DB_NAME = "ACAPortfolio"
_COL_NAME = "Cartera"


def main() -> None:
    apply = "--apply" in sys.argv[1:]
    col = get_mongo_client()[_DB_NAME][_COL_NAME]
    n = col.count_documents({})
    print(f"{_DB_NAME}.{_COL_NAME}: {n} docs.")
    if not apply:
        print("DRY-RUN — no se borró nada. Re-corré con --apply para vaciar.")
        return
    res = col.delete_many({})
    print(f"✅ Borrados {res.deleted_count} docs — la colección quedó vacía.")


if __name__ == "__main__":
    main()
