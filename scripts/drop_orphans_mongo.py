"""drop_orphans_mongo.py — dropea colecciones Mongo HUÉRFANAS (nadie lee NI escribe).

Verificado 2026-06-23 (grep exhaustivo de acceso REAL a la colección, no menciones en
comentarios/docstrings):
  - Trading.BondsMaster — ONs consolidadas en `Trading.Curvas` (curva=on_<sector>) desde
    2026-06-19. NINGÚN código accede la colección: `bonos_admin.py`/`ons.py`/`titulos_flujos.py`
    leen/escriben `Trading.Curvas`. La única mención a `Trading.BondsMaster` es el docstring
    "Fase 2 (pendiente)" de `comparar_inversion.py` (plan futuro, no código vivo). Huérfana.
  - CashFlow.Productores — ningún reader/writer en el repo. El único hit de "Productores" en
    el código (`segmentacion.py`) es el VALOR de nivel_1 de segmentación, NO la colección.
    Huérfana.

IRREVERSIBLE: dropear borra los datos. Dry-run por default muestra el conteo primero.
    python -m scripts.drop_orphans_mongo            # cuenta (dry-run)
    python -m scripts.drop_orphans_mongo --apply    # DROPEA
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client

_ORPHANS = [("Trading", "BondsMaster"), ("CashFlow", "Productores")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="dropear (default: dry-run)")
    args = ap.parse_args()

    cli = get_mongo_client()
    for db, coll in _ORPHANS:
        n = cli[db][coll].estimated_document_count()
        print(f"{db}.{coll}: {n:,} docs")
        if args.apply:
            cli[db][coll].drop()
            print(f"  ✅ {db}.{coll} DROPEADA")

    if not args.apply:
        print("\n(DRY-RUN — nada borrado. Correr con --apply.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
