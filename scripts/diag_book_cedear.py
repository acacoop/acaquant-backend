"""diag_book_cedear.py — por qué el LIBRO (DOM) de un CEDEAR no aparece.

El libro viene de `motor_rofex` (escribe `mercado.market_snapshot`), NO de
`motor_cedears`. El front, al seleccionar la card, suscribe el ticker on-demand
(`mercado.adhoc_subscriptions`) y el motor DEBERÍA escribir el book en
market_snapshot. Este diag mide dónde se corta la cadena, para un CEDEAR:

  1) ticker full resuelto + si existe en pyRofex (manager.pyrofex_instruments),
  2) si está suscripto (adhoc_subscriptions),
  3) si hay fila en market_snapshot y si trae book (bids/offers).

Read-only. Correr EN RUEDA. Uso:
    python -m scripts.diag_book_cedear RKLB
    python -m scripts.diag_book_cedear RKLB --plazo CI
"""
from __future__ import annotations

import argparse
import json

from psycopg.rows import dict_row

from core.postgres import get_pool


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker", help="ticker_corto (RKLB) o full ROFEX")
    ap.add_argument("--plazo", default="24hs", help="CI | 24hs | 48hs")
    a = ap.parse_args()
    full = a.ticker if " - " in a.ticker else f"MERV - XMEV - {a.ticker.strip()} - {a.plazo}"
    print(f"Ticker full: {full!r}\n")

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT 1 FROM manager.pyrofex_instruments WHERE instruments @> %s::jsonb LIMIT 1",
            (json.dumps([{"ticker": full}]),),
        )
        existe = cur.fetchone() is not None
        print(f"[1] existe en pyRofex (manager.pyrofex_instruments): {existe}")

        cur.execute(
            "SELECT ticker, expires_at, last_used_at FROM mercado.adhoc_subscriptions "
            "WHERE ticker = %s",
            (full,),
        )
        subs = cur.fetchall()
        print(f"[2] adhoc_subscriptions: {subs if subs else 'NO suscripto'}")

        cur.execute(
            "SELECT ticker, updated_at, book FROM mercado.market_snapshot WHERE ticker = %s",
            (full,),
        )
        ms = cur.fetchall()
        if not ms:
            print("[3] market_snapshot: NO hay fila (el motor no lo escribió)")
        else:
            for r in ms:
                b = r["book"] or {}
                nb = len(b.get("bids") or [])
                no = len(b.get("offers") or [])
                print(f"[3] market_snapshot: updated_at={r['updated_at']} · bids={nb} · offers={no}")

    print("\n=== CÓMO LEERLO ===")
    print("- [1] False → el CEDEAR no está en pyRofex con ese plazo (probá --plazo CI, o discovery no corrió).")
    print("- [2] NO suscripto → el front no llegó a suscribir (¿seleccionaste la card? ¿deployó Vercel?).")
    print("- [2] OK pero [3] sin fila / book vacío → motor_rofex NO está tomando el libro de ese CEDEAR.")
    print("- Todo OK con bids/offers > 0 → el backend está bien; el problema es del front/deploy.")


if __name__ == "__main__":
    main()
