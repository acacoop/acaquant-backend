"""diag_book_suscribir.py — test DECISIVO del backend del order book de un CEDEAR.

Suscribe UN CEDEAR a mano (mercado.adhoc_subscriptions) y observa si motor_rofex
le escribe el libro en `mercado.market_snapshot` en ~20s. Saca al frontend de la
ecuación: responde si el BACKEND puede servir la profundidad de un CEDEAR.

Correr EN RUEDA. Uso:
    python -m scripts.diag_book_suscribir RKLB
    python -m scripts.diag_book_suscribir RKLB --plazo CI
"""
from __future__ import annotations

import argparse
import time

from psycopg.rows import dict_row

from core.adhoc_subscriptions import subscribe
from core.postgres import get_pool


def _snap(full: str) -> str:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT updated_at, book FROM mercado.market_snapshot WHERE ticker = %s", (full,)
        )
        r = cur.fetchone()
    if not r:
        return "sin fila"
    b = r["book"] or {}
    return (f"updated_at={r['updated_at']} · bids={len(b.get('bids') or [])} "
            f"offers={len(b.get('offers') or [])}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--plazo", default="24hs", help="CI | 24hs | 48hs")
    a = ap.parse_args()
    full = a.ticker if " - " in a.ticker else f"MERV - XMEV - {a.ticker.strip()} - {a.plazo}"

    print(f"Ticker: {full!r}")
    print("ANTES:", _snap(full))
    print("subscribe():", subscribe(full))
    print("Esperando que motor_rofex lo levante (watcher 5s + snapshot 1s)…\n")
    for s in (5, 10, 15, 20, 25):
        time.sleep(5)
        print(f"+{s:2}s: {_snap(full)}")

    print("\n=== CÓMO LEERLO ===")
    print("- bids/offers > 0 con updated_at de HOY → motor_rofex SÍ da libro al CEDEAR.")
    print("  → el 'sin datos' es del front (no suscribía). Se arregla del lado front.")
    print("- sigue en 0/0 (o fila vieja) → motor_rofex NO toma el libro del CEDEAR.")
    print("  → hay que traer la profundidad por motor_cedears (que ya lo tiene suscripto).")


if __name__ == "__main__":
    main()
