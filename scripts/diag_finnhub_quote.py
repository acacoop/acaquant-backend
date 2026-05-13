"""diag_finnhub_quote.py — verifica que Finnhub /quote nos sirve para el
EOD close USD del subyacente de los CEDEARs.

Antes de codear un cron diario que pegue a Finnhub para alimentar
Trading.CedearsDaily, necesitamos confirmar:

  1. Que la API key free tier funciona.
  2. Que /quote devuelve los campos que necesitamos: c (current close),
     pc (previous close), h/l/o (high/low/open del día), t (timestamp).
  3. Que los tickers de nuestro universo (que son CEDEARs locales) tienen
     match directo en Finnhub usando el ticker corto (NVDA, AMD, SPY, etc.)
     sin sufijos raros.

Endpoint: GET https://finnhub.io/api/v1/quote?symbol={ticker}&token={key}

Response shape esperado:
  {
    "c":  226.13,    // Current price (post-close = last close)
    "h":  228.40,    // High of the day
    "l":  222.95,    // Low of the day
    "o":  224.04,    // Open of the day
    "pc": 224.62,    // Previous close — clave para return_usd_1d
    "t":  1729800001 // Unix timestamp
  }

Si c y pc vienen poblados con números, podemos calcular `return_usd_1d =
(c/pc - 1) × 100` directamente — exactamente el dato que necesitamos
para el cron EOD.

Uso (en el Droplet, con FINNHUB_API_KEY en .env):
    python -m scripts.diag_finnhub_quote
    python -m scripts.diag_finnhub_quote NVDA AMD     # tickers custom

Notas free tier:
  - 60 calls/min — sobra para 27 tickers + 1 SPY.
  - /quote sin restricciones para US equities.
  - /stock/candle (histórico) es PAGO — para backfill seguimos con
    Reuters CSV.
"""
from __future__ import annotations

import os
import sys
import time
import urllib.request
import urllib.parse
import json
from datetime import datetime, timezone

# Tickers default — un sample representativo del universo:
# - NVDA: semiconductores US
# - AMD:  semiconductores US (par natural de NVDA)
# - SPY:  benchmark de beta
# - BABA: chinese ADR (test de que ADR no-US también funciona)
# - YPF:  argentinian ADR (test de que ADR LatAm también funciona)
DEFAULT_TICKERS = ["NVDA", "AMD", "SPY", "BABA", "YPF"]

FINNHUB_BASE = "https://finnhub.io/api/v1/quote"


def fetch_quote(ticker: str, api_key: str) -> dict:
    """Llama a /quote y devuelve el JSON parseado o {'error': ...}.

    Errores típicos:
      - 401: key inválida.
      - 429: rate limit (60 calls/min).
      - 403: ticker fuera del free tier.
    """
    qs = urllib.parse.urlencode({"symbol": ticker, "token": api_key})
    url = f"{FINNHUB_BASE}?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            status = resp.status
        if status != 200:
            return {"error": f"HTTP {status}: {body[:200]}"}
        return json.loads(body)
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def fmt_value(v) -> str:
    if v is None:
        return "∅"
    if isinstance(v, (int, float)):
        return f"{v:.2f}"
    return str(v)


def run(tickers: list[str]) -> None:
    print("=" * 100)
    print("DIAG Finnhub /quote — validación del feed EOD USD para CedearsDaily")
    print("=" * 100)

    api_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not api_key:
        print("\n✗ FINNHUB_API_KEY no encontrada en env.")
        print("  Setear en el .env del Droplet: FINNHUB_API_KEY=xxx")
        print("  Sacar la key gratis en https://finnhub.io/register")
        return

    print(f"\nAPI key detectada: {api_key[:6]}…{api_key[-4:]} ({len(api_key)} chars)")
    print(f"Tickers a probar:  {tickers}")
    print(f"Endpoint:          {FINNHUB_BASE}")

    print(f"\n{'TICKER':<8} {'CLOSE':>10} {'PREV_CLOSE':>12} {'%1D':>8} "
          f"{'OPEN':>10} {'HIGH':>10} {'LOW':>10}  TS")
    print("─" * 100)

    ok_count = 0
    err_count = 0
    for ticker in tickers:
        q = fetch_quote(ticker, api_key)
        if "error" in q:
            print(f"{ticker:<8} ✗ {q['error']}")
            err_count += 1
            continue

        c  = q.get("c")
        pc = q.get("pc")
        o  = q.get("o")
        h  = q.get("h")
        l  = q.get("l")
        t  = q.get("t")

        # Sanity check: si c y pc vienen en 0, ticker no listado en Finnhub.
        if (c == 0 and pc == 0) or c is None:
            print(f"{ticker:<8} ⚠ Sin data: c={c} pc={pc} — ¿ticker mal escrito o fuera del free tier?")
            err_count += 1
            continue

        ret_1d = ((c / pc) - 1) * 100 if c and pc and pc != 0 else None
        ts_str = (
            datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            if t else "—"
        )
        print(f"{ticker:<8} {fmt_value(c):>10} {fmt_value(pc):>12} "
              f"{(f'{ret_1d:+.2f}%' if ret_1d is not None else '—'):>8} "
              f"{fmt_value(o):>10} {fmt_value(h):>10} {fmt_value(l):>10}  {ts_str}")
        ok_count += 1

        # Anti rate-limit defensivo (60/min ≈ 1/s; con 0.1s sobra).
        time.sleep(0.1)

    print("\n" + "=" * 100)
    print(f"Resultado: {ok_count} OK · {err_count} con error")
    print("=" * 100)
    if ok_count > 0:
        print("\nLectura:")
        print("• Si c (current) y pc (previous close) vienen con números reales →")
        print("  tenemos todo lo necesario para el cron EOD: return_usd_1d = (c/pc − 1) × 100.")
        print("• Si los tickers ARG (YPF, GGAL, VIST) responden bien → cubre todo el universo.")
        print("• Si algún ticker da 0/null → no lo cubre Finnhub free tier; alternativa Reuters.")
        print("\nPróximo paso (si todo OK): codear jobs/cedears_daily.py que pegue a /quote")
        print("para todos los tickers de Trading.Cedears y upsertee a Trading.CedearsDaily.")


if __name__ == "__main__":
    tickers = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_TICKERS
    run(tickers=[t.upper() for t in tickers])
