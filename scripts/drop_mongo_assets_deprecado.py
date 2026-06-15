"""scripts/drop_mongo_assets_deprecado.py — dropea las colecciones Mongo que
quedaron MUERTAS tras migrar el catálogo de títulos a SQL `portafolio.assets`.

Colecciones a dropear:
  - Valuaciones.Assets       → reemplazada por SQL portafolio.assets (panel + jobs + engine
                               + servicios ya leen/escriben SQL; ya no hay writers a Mongo).
  - Valuaciones.AuMResumenFCI → rollup FCI muerto (la serie FCI sale de portafolio.tenencia
                               filtrando cartera='FCI'; el job aum_resumen_fci fue eliminado).

PREREQUISITO (ya hecho en el código): nada lee ni escribe estas colecciones. Si todavía
no deployaste/reiniciaste la API con el código nuevo, NO corras el --commit.

    python -m scripts.drop_mongo_assets_deprecado            # PREVIEW (cuenta, no borra)
    python -m scripts.drop_mongo_assets_deprecado --commit   # DROPEA

Irreversible. Pero el dato vive en SQL (Assets) / se recalcula (AuMResumenFCI).
"""
from __future__ import annotations

import sys

from core.mongo import get_mongo_client

_OBJETIVO = [("Valuaciones", "Assets"), ("Valuaciones", "AuMResumenFCI")]


def main() -> None:
    commit = "--commit" in sys.argv
    client = get_mongo_client()
    print("\n=== Colecciones Mongo deprecadas (migradas a SQL) ===")
    for db, col in _OBJETIVO:
        existe = col in client[db].list_collection_names()
        n = client[db][col].estimated_document_count() if existe else 0
        print(f"   {db}.{col:<16} {'existe' if existe else 'NO existe':>10}  docs≈{n}")

    if not commit:
        print("\n[PREVIEW] no se borró nada. Repetí con --commit para DROPEAR.\n")
        return

    for db, col in _OBJETIVO:
        if col in client[db].list_collection_names():
            client[db].drop_collection(col)
            print(f"   ✓ dropeada {db}.{col}")
        else:
            print(f"   — {db}.{col} ya no existía")
    print("\n[COMMIT] listo. Chau Mongo Assets / AuMResumenFCI.\n")


if __name__ == "__main__":
    main()
