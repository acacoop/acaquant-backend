"""diag_finnhub_quote.py — valida que core.finnhub.quote sirve para el
EOD close USD del subyacente de los CEDEARs.

Usa el cliente ya configurado en core/finnhub.py (mismo que la watchlist).
Cero manejo de keys acá — viene de config.FINNHUB_API_KEY.

Para cada ticker, /quote devuelve:
  c  = last close USD
  pc = previous close USD
  o/h/l = open/high/low del día
  t  = timestamp epoch

Si c y pc vienen poblados → tenemos todo para el cron EOD que alimenta
Trading.CedearsDaily con return_usd_1d = (c/pc − 1) × 100.

Uso:
    python -m scripts.diag_finnhub_quote               # default sample
    python -m scripts.diag_finnhub_quote NVDA AMD      # tickers custom
    python -m scripts.diag_finnhub_quote --all         # todos los de Trading.Cedears activos
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from core.finnhub import FinnhubError, quote
from core.mongo import get_mongo_client


# Sample default — variado para ver que cubre US + ADR LatAm + ADR China + benchmark.
DEFAULT_TICKERS = ["NVDA", "AMD", "SPY", "BABA", "YPF"]


def _fmt(v) -> str:
    if v is None:
        return "∅"
    if isinstance(v, (int, float)):
        return f"{v:.2f}"
    return str(v)


def _tickers_activos() -> list[str]:
    """Todos los CEDEARs activos del master Trading.Cedears."""
    db = get_mongo_client()["Trading"]
    return sorted(
        d["ticker_corto"]
        for d in db["Cedears"].find({"activo": True}, {"_id": 0, "ticker_corto": 1})
    )


def run(tickers: list[str]) -> None:
    print("=" * 100)
    print(f"DIAG Finnhub /quote — {len(tickers)} tickers")
    print("=" * 100)

    print(f"\n{'TICKER':<8} {'CLOSE':>10} {'PREV':>10} {'%1D':>8} "
          f"{'OPEN':>10} {'HIGH':>10} {'LOW':>10}  TS")
    print("─" * 100)

    ok = 0
    fail = 0
    for ticker in tickers:
        try:
            q = quote(ticker)
        except FinnhubError as e:
            print(f"{ticker:<8} ✗ {e}")
            fail += 1
            continue

        c  = q.get("c")
        pc = q.get("pc")
        o  = q.get("o")
        h  = q.get("h")
        l  = q.get("l")
        t  = q.get("t")

        # Sanity check: c=pc=0 = ticker no listado en Finnhub free.
        if (c == 0 and pc == 0) or c is None:
            print(f"{ticker:<8} ⚠ Sin data — ticker fuera del free tier o no listado")
            fail += 1
            continue

        ret = ((c / pc) - 1) * 100 if c and pc else None
        ts_str = (
            datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            if t else "—"
        )
        print(f"{ticker:<8} {_fmt(c):>10} {_fmt(pc):>10} "
              f"{(f'{ret:+.2f}%' if ret is not None else '—'):>8} "
              f"{_fmt(o):>10} {_fmt(h):>10} {_fmt(l):>10}  {ts_str}")
        ok += 1

    print("\n" + "=" * 100)
    print(f"Resultado: {ok} OK · {fail} con error")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--all"]:
        run(_tickers_activos())
    else:
        run([t.upper() for t in args] if args else DEFAULT_TICKERS)
