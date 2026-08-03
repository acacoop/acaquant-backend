"""scripts/diag_estrategia_contexto.py — ¿por qué la tab ESTRATEGIA no muestra contexto?

Read-only. Recorre la cadena completa del panel CONTEXTO (ATR-20 + Efficiency
Ratio) para los tickers foco de config.ESTRATEGIA_CONTEXTO_TICKERS:

  1. mercado.cedears_ohlc_daily — ¿existe la columna `atr`? ¿cuántas ruedas hay
     por ticker? ¿la última rueda tiene atr o quedó NULL (falta historia)?
  2. mercado.cedears_time_sales — ¿hay tape de hoy? (el ER live sale de ahí; fuera
     de rueda el tape está vacío → er_dia/er_reciente = None, es esperado).
  3. La salida EXACTA del service (lo que devolvería GET /api/estrategia/contexto).

Uso:
    python -m scripts.diag_estrategia_contexto
"""
from __future__ import annotations

import json

import config
from core.postgres import connect


def main() -> int:
    tickers = config.ESTRATEGIA_CONTEXTO_TICKERS
    print(f"tickers foco: {tickers}\n")

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema = 'mercado' AND table_name = 'cedears_ohlc_daily'
               ORDER BY ordinal_position"""
        )
        cols = [r[0] for r in cur.fetchall()]
        if not cols:
            print("!! mercado.cedears_ohlc_daily NO EXISTE — el job nunca corrió.")
            return 1
        print(f"columnas cedears_ohlc_daily: {cols}")
        if "atr" not in cols:
            print("!! falta la columna `atr` → el job cedears_ohlc_daily no corrió "
                  "desde el deploy del ATR (su DDL la agrega).")

        cur.execute("SELECT count(DISTINCT fecha), min(fecha), max(fecha) "
                    "FROM mercado.cedears_ohlc_daily")
        n_ruedas, f_min, f_max = cur.fetchone()
        print(f"ruedas guardadas: {n_ruedas} ({f_min} → {f_max})  "
              f"[el ATR-20 necesita 21]\n")

        if "atr" in cols:
            cur.execute(
                """SELECT ticker_corto, count(*) AS ruedas,
                          count(atr) AS con_atr, max(fecha) AS ultima
                   FROM mercado.cedears_ohlc_daily
                   WHERE ticker_corto = ANY(%s)
                   GROUP BY ticker_corto ORDER BY ticker_corto""",
                (tickers,),
            )
            print("ticker    ruedas  con_atr  ultima")
            for tk, ruedas, con_atr, ultima in cur.fetchall():
                print(f"{tk:<9} {ruedas:>6} {con_atr:>8}  {ultima}")
            print()

        cur.execute(
            """SELECT ticker_corto, count(*) FROM mercado.cedears_time_sales
               WHERE ticker_corto = ANY(%s) GROUP BY ticker_corto ORDER BY 1""",
            (tickers,),
        )
        tape = cur.fetchall()
        print(f"tape vivo (cedears_time_sales): {tape or 'VACÍO (fuera de rueda → ER None)'}\n")

    from api.services import estrategia as svc
    print("salida de GET /api/estrategia/contexto:")
    print(json.dumps(svc.get_contexto(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
