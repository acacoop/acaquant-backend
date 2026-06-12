"""scripts/backfill_mercado_hist.py — backfill COMPLETO de la tabla mercado_hist.

`mercado_hist` (históricos diarios de BreakevensHistorico, ForwardsHistorico,
FuturosDLR, Caucion, FitParams, FairValueResiduos) se agregó al schema pero la
tabla no se había creado en Supabase → el sync incremental solo cubre los
últimos días. Esto la rellena ENTERA de una sola vez.

Seguro (REGLA #4): las colecciones fuente son chicas (~2.500 docs en total),
read-only sobre Mongo, upsert idempotente. Igual conviene correrlo fuera de
rueda. Requisito: la tabla debe existir → correr `python -m scripts.apply_schema`
primero.

Uso:
    python -m scripts.backfill_mercado_hist
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read
from core.postgres import get_pool
from jobs.sync_postgres import sync_mercado_hist


def main() -> int:
    mdb = get_mongo_client_read()
    with get_pool().connection() as conn:
        # desde=None → full (sin filtro de fecha). _upsert commitea internamente.
        n = sync_mercado_hist(mdb, conn, dry=False, desde=None)
    print(f"mercado_hist backfill OK: {n} filas upserteadas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
