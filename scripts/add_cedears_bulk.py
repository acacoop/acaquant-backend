"""add_cedears_bulk.py — alta MASIVA de los CEDEARs faltantes desde el CSV de clasificación.

Compara docs/cedears_clasificado_final.csv (universo objetivo) contra el master
`Trading.Cedears`, calcula los que faltan, y **VERIFICA contra BYMA** (pyRofex
get_market_data — la misma llamada que usa el motor en el arranque en frío) si cada
candidato tiene un CEDEAR ARS 24hs real ANTES de darlo de alta. Así no metemos
tickers basura (REGLA #2: no asumir que existe / que el símbolo BYMA == ticker US).

- Match contra el master por underlying/ticker_corto (case-insensitive).
- Probe ticker BYMA estándar: "MERV - XMEV - <TICKER> - 24hs".
  · status OK  → CEDEAR real → se da de alta (sin_cedear=False).
  · status !OK → no se encontró a ese símbolo → se REPORTA aparte, NO se da de alta
    (puede tener símbolo BYMA distinto o no tener cedear — lo revisás a mano).
- rubro/es_ia NO los toca este script: los pone `backfill_rubros_cedears` (matchea
  por underlying, ya cubre a los nuevos).

Dry-run por default (REGLA #4): probea y reporta, no escribe. --apply hace el upsert.
Idempotente (upsert por ticker_corto).

    python -m scripts.add_cedears_bulk            # probe + reporte (no escribe)
    python -m scripts.add_cedears_bulk --apply    # da de alta los verificados

Tras --apply (runbook):
    1. python -m scripts.backfill_rubros_cedears --apply # rubro/es_ia de los nuevos
    2. backfill EOD del underlying nuevo (precios_acciones_daily) + adr_live
    3. systemctl restart motor_cedears.service           # suscribe los tickers nuevos
SQL-native (decomiso 2026-06-29): el master es mercado.cedears (SQL), ya no Trading.Cedears.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import pyRofex
from psycopg.types.json import Jsonb

from core.postgres import get_pool
from core.rofex_session import inicializar_sesion

_CSV = Path(__file__).resolve().parent.parent / "docs" / "cedears_clasificado_final.csv"


def _leer_csv() -> list[dict]:
    with _CSV.open(encoding="utf-8") as f:
        return [
            {"ticker": (r["ticker"] or "").strip().upper(),
             "nombre": (r.get("nombre") or "").strip()}
            for r in csv.DictReader(f) if (r.get("ticker") or "").strip()
        ]


def _probe_cedear(ticker_corto: str) -> tuple[bool, str]:
    """True si existe un CEDEAR ARS 24hs a 'MERV - XMEV - <tc> - 24hs' (status OK)."""
    byma = f"MERV - XMEV - {ticker_corto} - 24hs"
    try:
        md = pyRofex.get_market_data(byma, entries=[pyRofex.MarketDataEntry.LAST])
        return (bool(md) and md.get("status") == "OK"), byma
    except Exception as e:  # connectividad / símbolo inválido
        return False, f"{byma}  (exc {type(e).__name__})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="da de alta (sin esto = dry-run)")
    args = ap.parse_args()

    filas = _leer_csv()
    print(f"CSV objetivo: {len(filas)} empresas")

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT underlying, ticker_corto FROM mercado.cedears")
        rows_ex = cur.fetchall()
    existentes = {(u or tc or "").upper() for u, tc in rows_ex}
    existentes |= {(tc or "").upper() for _, tc in rows_ex}
    print(f"Master mercado.cedears (SQL): {len(existentes)} símbolos ya cargados")

    faltan = [r for r in filas if r["ticker"] not in existentes]
    print(f"Faltan del CSV (no están en el master): {len(faltan)}\n")
    if not faltan:
        print("Nada para agregar ✅")
        return 0

    if not inicializar_sesion():
        print("❌ No se pudo iniciar sesión pyRofex — corré durante horario de mercado.")
        return 1

    con_cedear, sin_cedear = [], []
    for r in faltan:
        ok, byma = _probe_cedear(r["ticker"])
        (con_cedear if ok else sin_cedear).append({**r, "byma": byma})
        print(f"  {'✅' if ok else '··'} {r['ticker']:<8} {byma}")

    print(f"\nCON CEDEAR real (se dan de alta): {len(con_cedear)}")
    print(f"  {[r['ticker'] for r in con_cedear]}")
    print(f"\nSIN cedear a símbolo estándar (NO se agregan — revisar a mano): {len(sin_cedear)}")
    print(f"  {[r['ticker'] for r in sin_cedear]}")

    if not args.apply:
        print("\n(DRY-RUN — nada escrito. Re-corré con --apply para dar de alta los CON CEDEAR.)")
        return 0

    n = 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        for r in con_cedear:
            tc = r["ticker"]
            doc = {
                "ticker":       f"MERV - XMEV - {tc} - 24hs",
                "ticker_corto": tc,
                "underlying":   tc,
                "nombre":       r["nombre"] or tc,
                "sector":       None,
                "ratio_cedear": None,
                "sin_cedear":   False,
                "activo":       True,
            }
            # ON CONFLICT preserva rubro/es_ia (los pone el editor) — refresca el resto.
            cur.execute(
                "INSERT INTO mercado.cedears (ticker, ticker_corto, underlying, activo, data) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (ticker) DO UPDATE SET "
                "ticker_corto = EXCLUDED.ticker_corto, underlying = EXCLUDED.underlying, "
                "activo = EXCLUDED.activo, data = EXCLUDED.data",
                (doc["ticker"], tc, doc["underlying"], doc["activo"], Jsonb(doc)))
            n += 1
        conn.commit()
    print(f"\n✅ Alta de {n} CEDEARs en mercado.cedears (SQL).")
    print("Seguí el runbook del docstring: backfill_rubros_cedears --apply → backfill EOD/ADR "
          "→ restart motor_cedears.service.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
