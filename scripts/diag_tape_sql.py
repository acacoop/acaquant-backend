"""diag_tape_sql.py — por qué el TAPE (libro) sale vacío aunque mercado.timesales se llena.

Read-only. Clava la causa en una corrida: flag, cutoff de fecha, o match de ticker.

    python -m scripts.diag_tape_sql                       # diagnóstico general
    python -m scripts.diag_tape_sql --instrumento AL30    # + traza un instrumento puntual
"""
from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime, timedelta

from core.postgres import get_pool


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrumento", default="AL30", help="ticker corto o completo a trazar")
    args = ap.parse_args()

    print(f"RENTA_FIJA_SQL en el entorno: {os.getenv('RENTA_FIJA_SQL')!r}  "
          f"(si no es '1', /historico/trades cae al path MONGO → lee Trading.TimeSales DROPEADA → vacío)")

    art_hoy = (datetime.now(UTC) - timedelta(hours=3)).replace(
        tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    print(f"\ncutoff 'hoy' (naive ART) que usa get_historico_trades: {art_hoy}")

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*), min(ts), max(ts) FROM mercado.timesales")
        n_tot, ts_min, ts_max = cur.fetchone()
        print(f"\nmercado.timesales TOTAL: {n_tot:,} filas · ts min={ts_min} · ts max={ts_max}")

        cur.execute("SELECT count(*) FROM mercado.timesales WHERE ts >= %s", (art_hoy,))
        n_hoy = cur.fetchone()[0]
        print(f"mercado.timesales con ts >= cutoff hoy: {n_hoy:,} filas "
              f"{'← ⚠ 0 filas: o no hubo trades hoy, o el cutoff/TZ descarta todo' if n_hoy == 0 else '✅'}")

        print("\nTickers DISTINTOS con trades hoy (top 15 por volumen) — formato EXACTO guardado:")
        cur.execute(
            "SELECT ticker, count(*) n FROM mercado.timesales WHERE ts >= %s "
            "GROUP BY ticker ORDER BY n DESC LIMIT 15", (art_hoy,))
        for tk, n in cur.fetchall():
            print(f"   {n:>6}  |{tk}|")

    # Traza el instrumento puntual por el MISMO camino que la API.
    instr = args.instrumento
    print(f"\n── Traza de '{instr}' ──")
    from api.services.renta_fija import resolver_ticker_exacto
    exacto = resolver_ticker_exacto(instr)
    print(f"resolver_ticker_exacto({instr!r}) → {exacto!r}")
    if exacto:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM mercado.timesales WHERE ticker = %s AND ts >= %s",
                        (exacto, art_hoy))
            n = cur.fetchone()[0]
            print(f"mercado.timesales WHERE ticker = {exacto!r} AND ts>=hoy: {n:,} filas "
                  f"{'← ⚠ el ticker resuelto NO matchea el guardado (mirá los |...| de arriba)' if n == 0 else '✅ MATCHEA'}")

    from api.services.renta_fija_sql import get_historico_trades
    out = get_historico_trades(instrumento=instr)
    print(f"\nget_historico_trades(SQL, {instr!r}) devolvió: {len(out)} trades")
    out_all = get_historico_trades()
    print(f"get_historico_trades(SQL, SIN instrumento) devolvió: {len(out_all)} trades")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
