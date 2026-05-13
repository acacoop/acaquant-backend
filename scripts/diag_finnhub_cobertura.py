"""diag_finnhub_cobertura.py — testea qué tan lejos llegamos con Finnhub
sin Reuters/yfinance.

Dos pruebas:

  [A] HISTÓRICO via /stock/candle para NVDA (US underlying). 30 días.
      Si devuelve OHLCV poblado → no necesitamos Reuters para backfill.
      Si devuelve 403/no_data → /stock/candle se movió a paid tier y
      seguimos dependiendo de Reuters.

  [B] CEDEAR LOCAL BYMA vía /quote con varios formatos de symbol:
        - NVDA.BA            (Buenos Aires convention)
        - NVDA.BUE           (alternativa)
        - BCBA:NVDA          (con prefijo de exchange)
        - NVDA-AR            (sufijo país)
      Si alguno devuelve datos → Finnhub tiene cobertura de BYMA y
      podríamos cruzarlo contra nuestro CedearsSnapshot.

Uso: python -m scripts.diag_finnhub_cobertura
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from core.finnhub import FinnhubError, quote, stock_candle


def test_historico_us():
    print("=" * 100)
    print("[A] HISTÓRICO /stock/candle — NVDA US, últimos 30 días, resolution=D")
    print("=" * 100)
    end = int(datetime.now(timezone.utc).timestamp())
    start = int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp())

    try:
        res = stock_candle("NVDA", "D", start, end)
    except FinnhubError as e:
        print(f"  ✗ {e}")
        print("\n  Lectura: /stock/candle probable pasó a paid tier. "
              "Backfill histórico va por Reuters.")
        return False

    if not res or res.get("s") != "ok":
        print(f"  ⚠ status={res.get('s') if res else 'None'} ; no devolvió data")
        print(f"  raw: {res}")
        return False

    closes = res.get("c", [])
    timestamps = res.get("t", [])
    print(f"  ✓ {len(closes)} velas devueltas")
    if closes and timestamps:
        print(f"\n  {'Fecha':<12} {'OPEN':>10} {'HIGH':>10} {'LOW':>10} {'CLOSE':>10} {'VOL':>14}")
        opens = res.get("o", [])
        highs = res.get("h", [])
        lows  = res.get("l", [])
        vols  = res.get("v", [])
        # Primeros 5 + últimos 5 para no llenar la pantalla.
        idxs = list(range(min(5, len(closes)))) + list(range(max(5, len(closes) - 5), len(closes)))
        for i in idxs:
            ts = datetime.fromtimestamp(timestamps[i], tz=timezone.utc).strftime("%Y-%m-%d")
            print(f"  {ts:<12} {opens[i]:>10.2f} {highs[i]:>10.2f} "
                  f"{lows[i]:>10.2f} {closes[i]:>10.2f} {vols[i]:>14,.0f}")
    print("\n  Lectura: histórico OK — backfill puede hacerse 100% con Finnhub, sin Reuters.")
    return True


def test_cedears_byma():
    print("\n" + "=" * 100)
    print("[B] CEDEAR LOCAL BYMA via /quote — distintos formatos de symbol")
    print("=" * 100)

    # Probamos varias convenciones de symbol. Si alguno devuelve datos
    # poblados, Finnhub cubre BYMA y nuestro CedearsSnapshot puede
    # cruzarse contra esto.
    variantes = [
        "NVDA.BA",
        "NVDA.BUE",
        "BCBA:NVDA",
        "NVDA-AR",
        "AAPL.BA",   # segundo ticker para confirmar (NO solo una excepción)
    ]
    print(f"\n  {'SYMBOL':<15} {'CLOSE':>10} {'PREV':>10}  Comentario")
    print("  " + "─" * 95)
    cobertura_ok = False
    for sym in variantes:
        try:
            q = quote(sym)
        except FinnhubError as e:
            print(f"  {sym:<15} ✗ error: {e}")
            time.sleep(0.1)
            continue

        c  = q.get("c")
        pc = q.get("pc")
        if (c == 0 and pc == 0) or c is None:
            print(f"  {sym:<15} ⚠ sin data (c={c} pc={pc})")
        else:
            print(f"  {sym:<15} {c:>10.2f} {pc:>10.2f}  ← cubre BYMA")
            cobertura_ok = True
        time.sleep(0.1)

    print()
    if cobertura_ok:
        print("  Lectura: Finnhub cubre algún formato BYMA. Decidir si conviene cruzar")
        print("  vs el motor_cedears local (BYMA close ARS directo de pyRofex).")
    else:
        print("  Lectura: Finnhub NO cubre BYMA en el free tier. CEDEAR locales solo via")
        print("  pyRofex (lo que ya hace motor_cedears → CedearsSnapshot).")
        print("  Finnhub queda como fuente del SUBYACENTE US (close_underlying_usd).")


if __name__ == "__main__":
    test_historico_us()
    test_cedears_byma()
