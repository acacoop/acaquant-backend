"""Top rankings de actividad insider sobre Smart.Form4Transactions.

Imprime 6 vistas útiles directo a consola para validar el valor del dato
antes de armar service + frontend:

  1. Top 10 VENTAS open-market (code S) por USD
  2. Top 10 COMPRAS open-market (code P) por USD ← señal alcista pura
  3. CEOs / Officers que compraron su propia empresa (anything code P)
  4. Top tickers por actividad bruta (suma de |USD|)
  5. Insiders más activos (cantidad de transacciones)
  6. Actividad reciente (últimos 7 días)

Uso:
    python -m scripts.smart_money_top
    python -m scripts.smart_money_top --since 2026-01-01    # cutoff custom
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from core.mongo import get_mongo_client_read


def _fmt_usd(n: float | int | None) -> str:
    if n is None:
        return "—"
    a = abs(n)
    if a >= 1e9:
        return f"${n/1e9:>7.2f}B"
    if a >= 1e6:
        return f"${n/1e6:>7.2f}M"
    if a >= 1e3:
        return f"${n/1e3:>7.2f}K"
    return f"${n:>7.0f}"


def _line(c: str = "─", w: int = 90) -> None:
    print(c * w)


def _hdr(title: str) -> None:
    print()
    _line()
    print(f"  {title}")
    _line()


def top_ventas(col, since: str) -> None:
    _hdr(f"1. TOP 10 VENTAS OPEN-MARKET (S) — desde {since}")
    rows = list(col.aggregate([
        {"$match": {
            "tx_code": "S",
            "tx_type": "non_derivative",
            "filing_date": {"$gte": since},
            "value_usd": {"$gt": 0},
        }},
        {"$sort": {"value_usd": -1}},
        {"$limit": 10},
    ]))
    print(f"  {'#':>2} {'TICKER':<6} {'INSIDER':<28} {'ROLE':<14} {'USD':>10} {'FECHA':<11}")
    for i, r in enumerate(rows, 1):
        role = "Officer" if r.get("is_officer") else "Director" if r.get("is_director") else "10%+" if r.get("is_ten_percent_owner") else "Other"
        title = (r.get("officer_title") or "")[:13]
        role_str = f"{role}{('·'+title) if title else ''}"[:14]
        print(
            f"  {i:>2} {r['ticker_cedear']:<6} {r['insider_name'][:28]:<28} "
            f"{role_str:<14} {_fmt_usd(r['value_usd']):>10} "
            f"{r.get('transaction_date', '')[:10]:<11}"
        )


def top_compras_p(col, since: str) -> None:
    _hdr(f"2. TOP 10 COMPRAS OPEN-MARKET (P) — desde {since}  ← SEÑAL ALCISTA")
    rows = list(col.aggregate([
        {"$match": {
            "tx_code": "P",
            "tx_type": "non_derivative",
            "filing_date": {"$gte": since},
            "value_usd": {"$gt": 0},
        }},
        {"$sort": {"value_usd": -1}},
        {"$limit": 10},
    ]))
    if not rows:
        print("  (sin compras P en el período — eso por sí solo es información)")
        return
    print(f"  {'#':>2} {'TICKER':<6} {'INSIDER':<28} {'ROLE':<14} {'USD':>10} {'FECHA':<11}")
    for i, r in enumerate(rows, 1):
        role = "Officer" if r.get("is_officer") else "Director" if r.get("is_director") else "10%+"
        title = (r.get("officer_title") or "")[:13]
        role_str = f"{role}{('·'+title) if title else ''}"[:14]
        print(
            f"  {i:>2} {r['ticker_cedear']:<6} {r['insider_name'][:28]:<28} "
            f"{role_str:<14} {_fmt_usd(r['value_usd']):>10} "
            f"{r.get('transaction_date', '')[:10]:<11}"
        )


def ceos_compraron(col, since: str) -> None:
    _hdr(f"3. CEOS / OFFICERS que COMPRARON su propia empresa (P) — desde {since}")
    rows = list(col.aggregate([
        {"$match": {
            "tx_code": "P",
            "is_officer": True,
            "filing_date": {"$gte": since},
        }},
        {"$group": {
            "_id": {"ticker": "$ticker_cedear", "insider": "$insider_name", "title": "$officer_title"},
            "total_shares": {"$sum": "$shares"},
            "total_usd":    {"$sum": "$value_usd"},
            "n_tx":         {"$sum": 1},
            "first_date":   {"$min": "$transaction_date"},
            "last_date":    {"$max": "$transaction_date"},
        }},
        {"$sort": {"total_usd": -1}},
        {"$limit": 20},
    ]))
    if not rows:
        print("  (ningún officer compró en open-market en el período)")
        return
    print(f"  {'#':>2} {'TICKER':<6} {'INSIDER':<26} {'TITLE':<22} {'USD':>10} {'TX':>3} {'RANGO':<23}")
    for i, r in enumerate(rows, 1):
        k = r["_id"]
        title = (k.get("title") or "")[:22]
        rng = f"{(r['first_date'] or '')[:10]} → {(r['last_date'] or '')[:10]}"
        print(
            f"  {i:>2} {k['ticker']:<6} {k['insider'][:26]:<26} "
            f"{title:<22} {_fmt_usd(r['total_usd']):>10} {r['n_tx']:>3} {rng:<23}"
        )


def actividad_por_ticker(col, since: str) -> None:
    _hdr(f"4. TICKERS POR ACTIVIDAD INSIDER BRUTA — desde {since}")
    rows = list(col.aggregate([
        {"$match": {
            "filing_date": {"$gte": since},
            "value_usd":   {"$ne": None, "$gt": 0},
        }},
        {"$group": {
            "_id":           "$ticker_cedear",
            "gross_usd":     {"$sum": "$value_usd"},
            "n_tx":          {"$sum": 1},
            "n_insiders":    {"$addToSet": "$insider_name"},
            "n_sales":       {"$sum": {"$cond": [{"$eq": ["$tx_code", "S"]}, 1, 0]}},
            "n_buys":        {"$sum": {"$cond": [{"$eq": ["$tx_code", "P"]}, 1, 0]}},
            "usd_sales":     {"$sum": {"$cond": [{"$eq": ["$tx_code", "S"]}, "$value_usd", 0]}},
            "usd_buys":      {"$sum": {"$cond": [{"$eq": ["$tx_code", "P"]}, "$value_usd", 0]}},
        }},
        {"$sort": {"gross_usd": -1}},
        {"$limit": 15},
    ]))
    print(
        f"  {'#':>2} {'TICKER':<6} {'GROSS':>10} {'#TX':>4} {'#INSIDERS':>10} "
        f"{'SELLS$':>10} {'BUYS$':>10} {'NET':>10}"
    )
    for i, r in enumerate(rows, 1):
        net = r["usd_buys"] - r["usd_sales"]
        net_str = ("+" if net >= 0 else "") + _fmt_usd(net).strip()
        print(
            f"  {i:>2} {r['_id']:<6} {_fmt_usd(r['gross_usd']):>10} "
            f"{r['n_tx']:>4} {len(r['n_insiders']):>10} "
            f"{_fmt_usd(r['usd_sales']):>10} {_fmt_usd(r['usd_buys']):>10} {net_str:>10}"
        )


def insiders_mas_activos(col, since: str) -> None:
    _hdr(f"5. INSIDERS MÁS ACTIVOS por # transacciones — desde {since}")
    rows = list(col.aggregate([
        {"$match": {"filing_date": {"$gte": since}}},
        {"$group": {
            "_id": {"insider": "$insider_name", "ticker": "$ticker_cedear"},
            "n_tx":      {"$sum": 1},
            "gross_usd": {"$sum": {"$ifNull": ["$value_usd", 0]}},
            "title":     {"$first": "$officer_title"},
            "is_off":    {"$first": "$is_officer"},
            "is_dir":    {"$first": "$is_director"},
        }},
        {"$sort": {"n_tx": -1}},
        {"$limit": 15},
    ]))
    print(f"  {'#':>2} {'TICKER':<6} {'INSIDER':<28} {'ROLE/TITLE':<32} {'#TX':>4} {'GROSS':>10}")
    for i, r in enumerate(rows, 1):
        k = r["_id"]
        role = "Off" if r["is_off"] else "Dir" if r["is_dir"] else "—"
        title = (r.get("title") or "")[:25]
        rt = f"{role}{('·'+title) if title else ''}"[:32]
        print(
            f"  {i:>2} {k['ticker']:<6} {k['insider'][:28]:<28} "
            f"{rt:<32} {r['n_tx']:>4} {_fmt_usd(r['gross_usd']):>10}"
        )


def actividad_reciente(col) -> None:
    cutoff = (datetime.now(UTC) - timedelta(days=7)).date().isoformat()
    _hdr(f"6. ACTIVIDAD ÚLTIMOS 7 DÍAS (filed >= {cutoff})")
    rows = list(col.aggregate([
        {"$match": {"filing_date": {"$gte": cutoff}, "value_usd": {"$gt": 0}}},
        {"$sort": {"value_usd": -1}},
        {"$limit": 15},
    ]))
    if not rows:
        print("  (sin actividad en los últimos 7 días)")
        return
    print(
        f"  {'TICKER':<6} {'INSIDER':<26} {'CODE':<5} {'USD':>10} "
        f"{'TX DATE':<11} {'FILED':<11}"
    )
    for r in rows:
        print(
            f"  {r['ticker_cedear']:<6} {r['insider_name'][:26]:<26} "
            f"{r['tx_code']:<5} {_fmt_usd(r['value_usd']):>10} "
            f"{(r.get('transaction_date') or '')[:10]:<11} {r['filing_date']:<11}"
        )


def run(since: str) -> None:
    db = get_mongo_client_read()
    col = db["Smart"]["Form4Transactions"]
    total = col.count_documents({})
    if total == 0:
        print("Smart.Form4Transactions está vacía. Corré antes:")
        print("    python -m jobs.smart_money_form4")
        return
    print(f"\nSmart.Form4Transactions: {total:,} transacciones totales")
    top_ventas(col, since)
    top_compras_p(col, since)
    ceos_compraron(col, since)
    actividad_por_ticker(col, since)
    insiders_mas_activos(col, since)
    actividad_reciente(col)
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--since", default="2025-07-01",
        help="Cutoff de filing_date (default 2025-07-01)",
    )
    args = parser.parse_args()
    run(args.since)
