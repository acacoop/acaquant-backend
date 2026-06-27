"""Smoke test del MCP de RENTA VARIABLE — ejerce las 13 tools end-to-end.

Llama cada tool por el path real del MCP (`mcp.call_tool`), read-only, contra
los datos vivos. Elige un ticker del propio `rv_universo` para las tools por
papel. Reporta OK/FAIL + una muestra. NO escribe nada.

Uso (en el Droplet, raíz del repo):
    python -m scripts.smoke_mcp_rv
    python -m scripts.smoke_mcp_rv --ticker NVDA   # forzar un papel

Nota: las tools intradía (cedears_tape/intraday, day_trading_scanner) solo
traen datos en rueda; fuera de hora devuelven vacío y eso NO es un fallo.
"""
from __future__ import annotations

import argparse
import asyncio

from api.mcp.server import mcp


def _resumen(res) -> str:
    """Muestra compacta del retorno de una tool (lista o dict)."""
    # call_tool puede devolver (content_blocks, structured) o el valor directo.
    if isinstance(res, tuple) and len(res) == 2:
        res = res[1] if res[1] is not None else res[0]
    if isinstance(res, dict):
        if "result" in res and isinstance(res["result"], (list, dict)):
            res = res["result"]
    if isinstance(res, list):
        return f"list({len(res)})" + (f" ej={list(res[0])[:6]}" if res and isinstance(res[0], dict) else "")
    if isinstance(res, dict):
        return f"dict keys={list(res)[:8]}"
    return repr(res)[:120]


async def _run(name: str, args: dict) -> tuple[bool, str]:
    try:
        res = await mcp.call_tool(name, args)
        return True, _resumen(res)
    except Exception as e:  # smoke: queremos ver cualquier fallo
        return False, f"{type(e).__name__}: {e}"


async def main(ticker: str | None) -> int:
    # Resolver un ticker real desde el universo si no se forzó uno.
    if not ticker:
        from api.services import scanner_sql
        universo = scanner_sql.get_universo()
        ticker = next((u["ticker_corto"] for u in universo if u.get("ticker_corto")), "AAPL")
    print(f"Ticker de prueba: {ticker}\n")

    casos: list[tuple[str, dict]] = [
        ("rv_universo", {}),
        ("cedears_scanner", {}),
        ("ccl_live", {}),
        ("cedears_tape", {"ticker": ticker, "limite": 10}),
        ("cedears_intraday", {"ticker": ticker}),
        ("acciones_retornos", {"ticker": ticker}),
        ("acciones_quant_stats", {"ticker": ticker}),
        ("pivot_points", {"ticker": ticker}),
        ("day_trading_scanner", {"objetivo_pct": 0.5}),
        ("day_trading_companeros", {"ticker": ticker, "n": 5}),
        ("correlacion_matriz", {"tickers": f"{ticker},SPY,QQQ", "ventana_dias": 252}),
        ("trade_analysis", {"ticker": ticker, "monto": 10000, "direccion": "long"}),
        ("book_analysis", {"posiciones": f"{ticker}:10000,SPY:-5000"}),
    ]

    fallos = 0
    for name, args in casos:
        ok, detalle = await _run(name, args)
        marca = "✓" if ok else "✗ FAIL"
        if not ok:
            fallos += 1
        print(f"{marca:8} {name:24} {detalle}")

    print(f"\n{len(casos) - fallos}/{len(casos)} OK", "— TODO BIEN" if not fallos else f"— {fallos} FALLARON")
    return 1 if fallos else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default=None, help="ticker_corto BYMA a usar en las tools por papel")
    raise SystemExit(asyncio.run(main(ap.parse_args().ticker)))
