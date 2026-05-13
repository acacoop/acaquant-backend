"""diag_yfinance_cedears.py — testea yfinance para histórico OHLCV.

yfinance es scraper no oficial de Yahoo Finance. Gratis pero frágil
(se rompe cada 2-3 meses cuando Yahoo cambia el HTML). Acá probamos si
cubre los dos casos que Finnhub free no cubre:

  [A] US UNDERLYING — NVDA, 30 días daily. Histórico real para backfill.
  [B] BYMA LOCAL    — NVDA.BA / AMD.BA, 30 días. Si Yahoo tiene los
      CEDEARs locales con su precio ARS, podemos saltarnos Reuters.

Si yfinance no está instalado: pip install yfinance

Uso:
    python -m scripts.diag_yfinance_cedears
"""
from __future__ import annotations

from datetime import datetime, timedelta

try:
    import yfinance as yf
except ImportError:
    print("✗ yfinance no instalado. Instalar con:")
    print("    /root/TradingAV/venv/bin/pip install yfinance")
    raise SystemExit(1)


def _print_history(label: str, hist) -> None:
    print(f"\n  {label}")
    if hist is None or len(hist) == 0:
        print("    ∅ DataFrame vacío")
        return

    print(f"    {len(hist)} velas devueltas")
    print(f"\n    {'Fecha':<12} {'OPEN':>10} {'HIGH':>10} {'LOW':>10} {'CLOSE':>10} {'VOL':>14}")
    rows = list(hist.iterrows())
    # Primeros 5 + últimos 5
    idxs = list(range(min(5, len(rows)))) + list(range(max(5, len(rows) - 5), len(rows)))
    for i in idxs:
        idx, row = rows[i]
        fecha = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
        try:
            print(f"    {fecha:<12} {row['Open']:>10.2f} {row['High']:>10.2f} "
                  f"{row['Low']:>10.2f} {row['Close']:>10.2f} {row['Volume']:>14,.0f}")
        except (KeyError, TypeError, ValueError) as e:
            print(f"    {fecha:<12} ⚠ no se pudo formatear: {e}")


def run():
    print("=" * 100)
    print("DIAG yfinance — histórico US + BYMA")
    print("=" * 100)

    end   = datetime.now()
    start = end - timedelta(days=30)

    # ── [A] US UNDERLYING ──────────────────────────────────────────
    print("\n[A] NVDA (US underlying) — 30 días daily")
    print("=" * 100)
    try:
        nvda = yf.Ticker("NVDA")
        hist = nvda.history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))
        _print_history("Histórico NVDA US:", hist)
    except Exception as e:
        print(f"    ✗ Exception: {type(e).__name__}: {e}")

    # ── [B] BYMA LOCAL ────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("[B] CEDEARs BYMA — NVDA.BA + AMD.BA + AAPL.BA + GGAL.BA + YPFD.BA")
    print("=" * 100)
    for sym in ["NVDA.BA", "AMD.BA", "AAPL.BA", "GGAL.BA", "YPFD.BA"]:
        try:
            t = yf.Ticker(sym)
            hist = t.history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))
            _print_history(f"{sym}:", hist)
        except Exception as e:
            print(f"\n  {sym}: ✗ Exception: {type(e).__name__}: {e}")

    print("\n" + "=" * 100)
    print("LECTURA")
    print("=" * 100)
    print("• Si [A] devuelve 30 velas pobladas → backfill US underlying via yfinance OK.")
    print("• Si [B] algún .BA devuelve velas → BYMA local también cubierto, opcional.")
    print("• Si todo falla → única vía histórica es Reuters CSV.")


if __name__ == "__main__":
    run()
