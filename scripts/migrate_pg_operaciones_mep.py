"""scripts/migrate_pg_operaciones_mep.py — agrega la columna `mep` a la tabla
Postgres `operaciones` (faltaba: cuando se migró la vista OPERACIONES a SQL solo
se necesitaba volumen por moneda nativa, no dolarizar → `mep` nunca se sincronizó).

POR QUÉ: sin `mep` por-boleto en SQL no se puede dolarizar el volumen de
OPERACIONES (la tabla `negocio_movimientos` sí lo tiene; `operaciones` no). Esta
migración deja las dos tablas parejas con Mongo en ese campo.

Idempotente: `ADD COLUMN IF NOT EXISTS` → re-correrlo no rompe nada. Solo crea la
columna (vacía); el backfill de los valores lo hace el sync (jobs.sync_postgres).

Uso (en el Droplet):
    python -m scripts.migrate_pg_operaciones_mep
    python -m jobs.sync_postgres --full       # backfill: llena mep en toda la tabla
"""
from __future__ import annotations

from core.postgres import connect


def main() -> int:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("ALTER TABLE operaciones ADD COLUMN IF NOT EXISTS mep numeric")
        conn.commit()
        cur.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'operaciones' AND column_name = 'mep'"
        )
        existe = cur.fetchone()[0]
    print(f"OK — columna operaciones.mep {'presente' if existe else 'NO creada (?)'}.")
    print("Siguiente paso: python -m jobs.sync_postgres --full  (backfillea mep).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
