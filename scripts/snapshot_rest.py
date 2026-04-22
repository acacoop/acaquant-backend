"""Toma el último tick disponible via pyRofex REST y escribe en
Trading.MarketSnapshot con el mismo shape que el motor_rofex (via WS).

Útil cuando agregás un bono fuera de rueda de mercado y querés que
aparezca en la tabla RENTA FIJA sin esperar al próximo día operativo:
- El motor_rofex solo escribe MarketSnapshot cuando recibe ticks del WS.
- Fuera de rueda el WS no manda nada → la tabla no lista el ticker.
- Este script llama al REST (get_market_data) que devuelve el último
  estado del libro del día y construye el doc manualmente.

Shape escrito (idéntico al de engines/valores.py:_snapshot_loop):
    {
        ticker, updated_at,
        book: {bids: [...top5], offers: [...top5]},
        metrics: {last_price, vwap, total_nominals, open_price, high_price,
                  low_price, closing_price, total_money, buy_money, sell_money},
        hourly_stats: {}, top_trades: [], recent_trades: []
    }

Uso:
    python -m scripts.snapshot_rest GD29D GD30D GD35D GD38D GD41D
    python -m scripts.snapshot_rest --all-soberanos
    python -m scripts.snapshot_rest "MERV - XMEV - GD30D - 24hs"
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
from typing import Any

import pyRofex
from pymongo import ReplaceOne

from core.mongo import get_mongo_client
from core.rofex_session import inicializar_sesion


def _resolver_tickers(client, inputs: list[str], all_soberanos: bool) -> list[tuple[str, str]]:
    """Devuelve lista de (ticker_full, ticker_corto)."""
    curvas = client["Trading"]["Curvas"]
    out: list[tuple[str, str]] = []
    if all_soberanos:
        for d in curvas.find({"curva": "soberanos"}, {"_id": 0, "ticker": 1, "ticker_corto": 1}):
            if d.get("ticker") and d.get("ticker_corto"):
                out.append((d["ticker"], d["ticker_corto"]))
        return out
    for x in inputs:
        if " - " in x:
            doc = curvas.find_one({"ticker": x}, {"_id": 0, "ticker": 1, "ticker_corto": 1})
        else:
            doc = curvas.find_one({"ticker_corto": x}, {"_id": 0, "ticker": 1, "ticker_corto": 1})
        if not doc:
            print(f"  [skip] no encontrado en Trading.Curvas: {x}")
            continue
        out.append((doc["ticker"], doc["ticker_corto"]))
    return out


def _parse_ticker_full(ticker_full: str) -> dict[str, str]:
    """pyRofex espera {marketId: ..., symbol: ...} — extraemos del ticker completo."""
    return {"marketId": "ROFX", "symbol": ticker_full}


def _book_levels(entries_side: Any) -> list[dict]:
    """Normaliza el formato de pyRofex (list de dicts con price/size) a top-5."""
    if not entries_side:
        return []
    if isinstance(entries_side, list):
        return [
            {"price": lvl.get("price"), "size": lvl.get("size")}
            for lvl in entries_side[:5]
            if lvl.get("price") is not None
        ]
    return []


def _build_snapshot_doc(ticker_full: str, md: dict, ts: datetime) -> dict:
    """Arma el doc de MarketSnapshot desde la respuesta de get_market_data.

    pyRofex devuelve: {status: "OK", marketData: {LA, BI, OF, OP, HI, LO, CL, NV, EV, ...}}
    """
    entries = md.get("marketData", {}) or {}
    la = entries.get("LA") or {}
    bi = entries.get("BI")
    of = entries.get("OF")

    last_price = la.get("price")
    total_nominals = float(entries.get("NV") or 0)
    total_money = float(entries.get("EV") or 0)
    vwap = round(total_money / total_nominals, 4) if total_nominals > 0 else last_price

    return {
        "ticker":     ticker_full,
        "updated_at": ts,
        "book": {
            "bids":   _book_levels(bi),
            "offers": _book_levels(of),
        },
        "metrics": {
            "last_price":     last_price,
            "vwap":           vwap,
            "total_nominals": total_nominals,
            "total_money":    total_money,
            "open_price":     entries.get("OP"),
            "high_price":     entries.get("HI"),
            "low_price":      entries.get("LO"),
            "closing_price":  entries.get("CL"),
            "buy_money":      0.0,
            "sell_money":     0.0,
        },
        "hourly_stats":  {},
        "top_trades":    [],
        "recent_trades": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tickers", nargs="*",
                        help="Tickers a snapshotear (corto o completo)")
    parser.add_argument("--all-soberanos", action="store_true",
                        help="Toma todos los tickers de Trading.Curvas con curva='soberanos'")
    args = parser.parse_args()

    if not args.tickers and not args.all_soberanos:
        parser.error("Pasá al menos un ticker o --all-soberanos")

    client = get_mongo_client()
    tickers = _resolver_tickers(client, args.tickers, args.all_soberanos)
    if not tickers:
        print("✗ Sin tickers para procesar.")
        return 1

    print("Autenticando con pyRofex...")
    if not inicializar_sesion():
        print("✗ No pude iniciar sesión pyRofex.")
        return 2

    entries = [
        pyRofex.MarketDataEntry.LAST,
        pyRofex.MarketDataEntry.BIDS,
        pyRofex.MarketDataEntry.OFFERS,
        pyRofex.MarketDataEntry.OPENING_PRICE,
        pyRofex.MarketDataEntry.HIGH_PRICE,
        pyRofex.MarketDataEntry.LOW_PRICE,
        pyRofex.MarketDataEntry.CLOSING_PRICE,
        pyRofex.MarketDataEntry.TRADE_VOLUME,           # NV en el response
        pyRofex.MarketDataEntry.TRADE_EFFECTIVE_VOLUME, # EV en el response
    ]

    col = client["Trading"]["MarketSnapshot"]
    ops = []
    ts = datetime.now(UTC)

    print(f"\n{'Ticker':<45}  {'Last':>10}  {'Bid':>10}  {'Offer':>10}  {'Vol NV':>12}")
    print("-" * 95)

    for ticker_full, ticker_corto in tickers:
        try:
            md = pyRofex.get_market_data(_parse_ticker_full(ticker_full), entries=entries)
        except Exception as e:
            print(f"  [err] {ticker_full}: {e}")
            continue

        if md.get("status") != "OK":
            print(f"  [!= OK] {ticker_full}: {md}")
            continue

        doc = _build_snapshot_doc(ticker_full, md, ts)
        m = doc["metrics"]
        bid = doc["book"]["bids"][0]["price"] if doc["book"]["bids"] else None
        offer = doc["book"]["offers"][0]["price"] if doc["book"]["offers"] else None
        print(
            f"{ticker_corto:<10} {ticker_full:<34}  "
            f"{m['last_price']!s:>10}  "
            f"{bid!s:>10}  "
            f"{offer!s:>10}  "
            f"{m['total_nominals']:>12,.0f}"
        )

        ops.append(ReplaceOne({"ticker": ticker_full}, doc, upsert=True))

    if not ops:
        print("\n✗ Ningún ticker devolvió datos válidos.")
        return 3

    r = col.bulk_write(ops, ordered=False)
    matched = getattr(r, "matched_count", 0)
    upserted = len(getattr(r, "upserted_ids", {}) or {})
    print(f"\n✓ MarketSnapshot: {matched} reemplazados, {upserted} insertados (total {len(ops)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
