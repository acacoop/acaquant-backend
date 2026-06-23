"""diag_tape_sql.py — por qué el TAPE (libro) sale vacío aunque mercado.timesales se llena.

Read-only. Replica EXACTO el camino del frontend: toma el mismo ticker que muestra el
libro (el de mayor volumen de get_renta_fija) y lo traza punta a punta.

    python -m scripts.diag_tape_sql                  # usa el ticker[0] real del front
    python -m scripts.diag_tape_sql --instrumento X  # fuerza uno puntual
"""
from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime, timedelta

from core.postgres import get_pool


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrumento", default=None)
    args = ap.parse_args()

    print(f"RENTA_FIJA_SQL = {os.getenv('RENTA_FIJA_SQL')!r}")

    art_hoy = (datetime.now(UTC) - timedelta(hours=3)).replace(
        tzinfo=None, hour=0, minute=0, second=0, microsecond=0)

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM mercado.timesales WHERE ts >= %s", (art_hoy,))
        print(f"timesales con ts >= hoy({art_hoy}): {cur.fetchone()[0]:,} filas")

    # El MISMO ticker que el front muestra por default: get_renta_fija ordenado por
    # total_nominals desc, primer instrumento con last_price (igual que libro-panel.tsx).
    from api.services.renta_fija_sql import get_historico_trades, get_renta_fija
    rf = get_renta_fija()
    con_last = [d for d in rf if d.get("metrics", {}).get("last_price")]
    con_last.sort(key=lambda d: d.get("metrics", {}).get("total_nominals", 0), reverse=True)
    instr = args.instrumento or (con_last[0]["instrumento"] if con_last else None)
    print(f"\nticker que el FRONT mandaría (instrumento[0]): {instr!r}")
    print(f"primeros 5 instrumentos de get_renta_fija: {[d['instrumento'] for d in con_last[:5]]}")

    if instr:
        from api.services.renta_fija import resolver_ticker_exacto
        exacto = resolver_ticker_exacto(instr)
        print(f"\nresolver_ticker_exacto({instr!r}) → {exacto!r}")
        with get_pool().connection() as conn, conn.cursor() as cur:
            # Exact (lo que hace hoy get_historico_trades).
            if exacto:
                cur.execute("SELECT count(*) FROM mercado.timesales WHERE ticker = %s AND ts >= %s",
                            (exacto, art_hoy))
                print(f"  EXACTO  ticker = {exacto!r}  → {cur.fetchone()[0]:,} filas")
            # Substring (lo que haría un ILIKE, como get_renta_fija).
            esc = instr.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            cur.execute("SELECT count(*) FROM mercado.timesales WHERE ticker ILIKE %s ESCAPE '\\' "
                        "AND ts >= %s", (f"%{esc}%", art_hoy))
            print(f"  ILIKE   '%{instr}%'  → {cur.fetchone()[0]:,} filas")

        # ¿Existe el SÍMBOLO (sin settlement) bajo CUALQUIER ticker en timesales hoy?
        simbolo = instr.split(" - ")[2] if " - " in instr else instr
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT DISTINCT ticker FROM mercado.timesales "
                        "WHERE ticker ILIKE %s AND ts >= %s LIMIT 10",
                        (f"%{simbolo}%", art_hoy))
            encontrados = [r[0] for r in cur.fetchall()]
        print(f"\ntimesales con símbolo '{simbolo}' (cualquier settlement) hoy: "
              f"{encontrados or '⚠ NINGUNO — ese bono no operó en el tape hoy'}")

        n = len(get_historico_trades(instrumento=instr))
        print(f"get_historico_trades(SQL, {instr!r}) = {n} trades  "
              f"{'← VACÍO' if n == 0 else '✅'}")

    # SANITY: agarro el ticker de MAYOR volumen en timesales hoy y lo paso por el
    # mismo get_historico_trades. Si esto da >0, el backend del tape FUNCIONA.
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, count(*) n FROM mercado.timesales WHERE ts >= %s "
                    "GROUP BY ticker ORDER BY n DESC LIMIT 1", (art_hoy,))
        row = cur.fetchone()
    if row:
        top_tk, top_n = row
        m = len(get_historico_trades(instrumento=top_tk))
        print(f"\nSANITY — ticker más operado hoy: {top_tk!r} ({top_n:,} trades en timesales)")
        print(f"  get_historico_trades({top_tk!r}) = {m} trades  "
              f"{'← ⚠ BUG: el backend NO devuelve trades que SÍ existen' if m == 0 else '✅ el backend FUNCIONA'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
