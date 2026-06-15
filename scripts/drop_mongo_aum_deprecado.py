"""scripts/drop_mongo_aum_deprecado.py — dropea Mongo `Valuaciones.AuM` y la tabla
SQL `aum` (espejo), ya deprecadas tras migrar las tenencias a `portafolio.tenencia`.

Qué se dropea:
  - Mongo Valuaciones.AuM  → reemplazada por SQL portafolio.tenencia (writer diario
                             jobs.portafolio_backfill --diario). Lectores vivos migrados,
                             writer Mongo (jobs.aum/backfills) eliminado.
  - SQL tabla `aum`        → la sincronizaba sync_postgres.sync_aum (ELIMINADO);
                             pnl_sql/comercial_sql ahora leen portafolio.tenencia.

PREREQUISITO: deployar el código nuevo + restart api ANTES de correr el --commit
(así nadie lee/escribe AuM). Las ramas Mongo de portfolio/valuaciones/pnl/comercial
NO se ejecutan (flags SQL ON) → seguras.

    python -m scripts.drop_mongo_aum_deprecado            # PREVIEW (cuenta, no borra)
    python -m scripts.drop_mongo_aum_deprecado --commit   # DROPEA Mongo AuM + tabla SQL aum

Irreversible. El dato vive en portafolio.tenencia.
"""
from __future__ import annotations

import sys

from core.mongo import get_mongo_client
from core.postgres import get_pool


def main() -> None:
    commit = "--commit" in sys.argv
    client = get_mongo_client()

    existe = "AuM" in client["Valuaciones"].list_collection_names()
    n_mongo = client["Valuaciones"]["AuM"].estimated_document_count() if existe else 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.aum')")
        tabla = cur.fetchone()[0]
        n_sql = 0
        if tabla:
            cur.execute("SELECT count(*) FROM aum")
            n_sql = cur.fetchone()[0]

        print("\n=== AuM deprecado (migrado a portafolio.tenencia) ===")
        print(f"   Mongo Valuaciones.AuM : {'existe' if existe else 'NO existe':>10}  docs≈{n_mongo:,}")
        print(f"   SQL tabla `aum`       : {'existe' if tabla else 'NO existe':>10}  filas={n_sql:,}")

        if not commit:
            print("\n[PREVIEW] no se borró nada. Repetí con --commit para DROPEAR.\n")
            return

        if existe:
            client["Valuaciones"].drop_collection("AuM")
            print("   ✓ dropeada Mongo Valuaciones.AuM")
        if tabla:
            cur.execute("DROP TABLE IF EXISTS aum")
            conn.commit()
            print("   ✓ dropeada SQL tabla `aum`")
        print("\n[COMMIT] listo. Chau Mongo AuM.\n")


if __name__ == "__main__":
    main()
