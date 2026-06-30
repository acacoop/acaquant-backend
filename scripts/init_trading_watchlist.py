"""Crea la tabla manager.trading_watchlist (vista TRADING) — idempotente.

schema.sql no siempre está aplicado en la DB real (ver CLAUDE.md), así que la
watchlist de la vista /trading necesita este create explícito. Correr UNA vez
tras el deploy:

    python -m scripts.init_trading_watchlist

CREATE TABLE IF NOT EXISTS → re-correrlo no rompe nada. Una vez confirmado que la
tabla existe en prod, este script se puede borrar (REGLA #5).
"""
from __future__ import annotations

from core.postgres import get_pool

DDL = """
CREATE TABLE IF NOT EXISTS manager.trading_watchlist (
    email      text PRIMARY KEY,
    tickers    text[] NOT NULL DEFAULT '{}',
    updated_at timestamptz NOT NULL DEFAULT now()
);
"""


def main() -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(DDL)
        conn.commit()
        cur.execute(
            "SELECT to_regclass('manager.trading_watchlist') IS NOT NULL AS ok"
        )
        ok = cur.fetchone()[0]
    print(f"manager.trading_watchlist {'OK' if ok else 'NO se creó'}")


if __name__ == "__main__":
    main()
