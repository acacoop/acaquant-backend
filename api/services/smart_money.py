"""Service puro — Smart Money (13F institucionales + Form 4 insiders).

Consume:
  - Smart.CEDEARsCatalog       → universo de tickers (36 CEDEARs)
  - Smart.Holdings13F          → holdings institucionales por (filer, cusip, quarter)
  - Smart.Form4Transactions    → trades de insiders (CEOs, directors, officers)
  - Smart.Managers             → managers descubiertos automáticamente

Queries que expone (todas cacheadas, sin mutaciones):

  - get_managers_list()         → lista de managers descubiertos
  - get_manager_portfolio(cik)  → portfolio actual + cambios Q-on-Q (filtrado a CEDEARs)
  - get_ticker_flow(ticker)     → todo el smart money sobre un ticker (13F + Form 4 + Q-on-Q)
  - get_cohort_overview()       → top buys/sells, consensus, divergences
  - get_recent_activity(days)   → últimas N días de Form 4 + 13F
  - get_insiders_recent_top()   → top insiders por valor reciente (compras/ventas)

Notas técnicas:
  - El field `value` del 13F XML es USD directo desde enero 2023 (no MILES).
  - report_date se aproxima desde filing_date — las 13F-HR tienen deadline
    45 días post-quarter, así que un filing del 2026-02-17 está reportando
    Q4 2025 (2025-12-31). Mapeo via _filing_date_to_report_date().
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from api.cache import cached
from core.mongo import get_mongo_client_read

# ─────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────────────────────────────────────


def _filing_date_to_report_date(filing_date: str | None) -> str | None:
    """Infiere el período reportado de un 13F-HR a partir de la filing_date.

    13F deadline = 45 días después del cierre de quarter. Mapeo aprox:
      Filed Jan 1 → Feb 14   → reports Q3 prev year (Sep 30)
      Filed Feb 15 → May 14  → reports Q4 prev year (Dec 31)
      Filed May 15 → Aug 14  → reports Q1 (Mar 31)
      Filed Aug 15 → Nov 14  → reports Q2 (Jun 30)
      Filed Nov 15 → Dec 31  → reports Q3 (Sep 30)

    Aproximación >95% precisa. Amendments tardíos podrían fallar — bug
    cosmético, no rompe queries.
    """
    if not filing_date:
        return None
    try:
        d = datetime.strptime(filing_date[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    md = d.month * 100 + d.day
    year = d.year
    if md <= 214:
        return f"{year - 1}-09-30"
    if md <= 514:
        return f"{year - 1}-12-31"
    if md <= 814:
        return f"{year}-03-31"
    if md <= 1114:
        return f"{year}-06-30"
    return f"{year}-09-30"


def _db():
    return get_mongo_client_read()


def _cedear_universe() -> dict[str, dict]:
    """Devuelve {cusip: {ticker, nombre_corto, cik_issuer}}."""
    db = _db()
    out: dict[str, dict] = {}
    for d in db["Smart"]["CEDEARsCatalog"].find(
        {"is_active": True},
        {"_id": 0, "ticker": 1, "cusip": 1, "nombre_corto": 1, "cik_issuer": 1},
    ):
        out[d["cusip"]] = d
    return out


def _cedear_by_ticker() -> dict[str, dict]:
    return {v["ticker"]: v for v in _cedear_universe().values()}


# ─────────────────────────────────────────────────────────────────────────────
# 1. Managers list
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=300)
def get_managers_list() -> list[dict]:
    """Lista de managers descubiertos por el ingest 13F. Sorted por last_filing_date desc."""
    db = _db()
    rows = list(db["Smart"]["Managers"].find(
        {},
        {"_id": 0},
    ).sort("last_filing_date", -1))
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# 2. Manager portfolio
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=300)
def get_manager_portfolio(cik: str) -> dict[str, Any]:
    """Portfolio CEDEAR del manager — último Q + Q-on-Q diffs.

    Devuelve:
      {
        "manager": {cik, name, last_filing_date},
        "current_quarter": "2025-12-31",
        "previous_quarter": "2025-09-30",
        "n_holdings_cedear": N,
        "total_value_usd": $,
        "holdings": [
          {ticker, shares, value_usd, n_lines, pct_of_cedear_portfolio,
           prev_shares, prev_value_usd, status (NEW|INCREASED|REDUCED|UNCHANGED|EXITED),
           shares_delta_pct}
        ]
      }
    """
    cik = cik.lstrip("0")
    db = _db()
    manager_doc = db["Smart"]["Managers"].find_one({"cik": cik}, {"_id": 0})
    if not manager_doc:
        return {"error": f"manager {cik} no encontrado"}

    # Cargo TODOS los holdings de este filer, agrupo por (cusip, report_date).
    rows = list(db["Smart"]["Holdings13F"].find(
        {"filer_cik": cik},
        {"_id": 0, "cusip": 1, "ticker_cedear": 1, "shares": 1, "value_usd": 1, "filing_date": 1},
    ))

    # Agrupo por (report_date, cusip).
    by_quarter: dict[str, dict[str, dict]] = {}
    for r in rows:
        rd = _filing_date_to_report_date(r.get("filing_date"))
        if not rd:
            continue
        slot = by_quarter.setdefault(rd, {})
        cusip = r["cusip"]
        agg = slot.setdefault(cusip, {
            "ticker": r["ticker_cedear"],
            "shares": 0,
            "value_usd": 0,
            "n_lines": 0,
        })
        agg["shares"] += r.get("shares") or 0
        agg["value_usd"] += r.get("value_usd") or 0
        agg["n_lines"] += 1

    if not by_quarter:
        return {
            "manager": manager_doc,
            "current_quarter": None,
            "holdings": [],
        }

    sorted_quarters = sorted(by_quarter.keys(), reverse=True)
    current_q = sorted_quarters[0]
    previous_q = sorted_quarters[1] if len(sorted_quarters) > 1 else None
    curr = by_quarter[current_q]
    prev = by_quarter.get(previous_q) or {}

    total_value = sum(h["value_usd"] for h in curr.values())

    holdings_out: list[dict] = []
    for cusip, h in curr.items():
        prev_h = prev.get(cusip)
        if prev_h is None:
            status = "NEW"
            delta_pct = None
        elif h["shares"] > prev_h["shares"]:
            status = "INCREASED"
            delta_pct = (
                ((h["shares"] - prev_h["shares"]) / prev_h["shares"]) * 100
                if prev_h["shares"] else None
            )
        elif h["shares"] < prev_h["shares"]:
            status = "REDUCED"
            delta_pct = (
                ((h["shares"] - prev_h["shares"]) / prev_h["shares"]) * 100
                if prev_h["shares"] else None
            )
        else:
            status = "UNCHANGED"
            delta_pct = 0.0
        holdings_out.append({
            "ticker": h["ticker"],
            "cusip": cusip,
            "shares": h["shares"],
            "value_usd": h["value_usd"],
            "n_lines": h["n_lines"],
            "pct_of_cedear_portfolio": (
                (h["value_usd"] / total_value * 100) if total_value else 0
            ),
            "status": status,
            "shares_delta_pct": delta_pct,
            "prev_shares": prev_h["shares"] if prev_h else None,
            "prev_value_usd": prev_h["value_usd"] if prev_h else None,
        })

    # Exited: estaban en prev pero no en curr.
    exited: list[dict] = []
    for cusip, prev_h in prev.items():
        if cusip not in curr:
            exited.append({
                "ticker": prev_h["ticker"],
                "cusip": cusip,
                "shares": 0,
                "value_usd": 0,
                "status": "EXITED",
                "shares_delta_pct": -100.0,
                "prev_shares": prev_h["shares"],
                "prev_value_usd": prev_h["value_usd"],
            })

    holdings_out.sort(key=lambda x: -x["value_usd"])

    return {
        "manager":            manager_doc,
        "current_quarter":    current_q,
        "previous_quarter":   previous_q,
        "n_holdings_cedear":  len(holdings_out),
        "total_value_usd":    total_value,
        "holdings":           holdings_out,
        "exited":             exited,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3. Ticker flow (la pantalla central)
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=300)
def get_ticker_flow(ticker: str) -> dict[str, Any]:
    """Flow completo sobre un CEDEAR: 13F institucional + Form 4 insiders.

    Devuelve dict con:
      - meta: {ticker, cusip, nombre_corto, cik_issuer}
      - institutional_13f: {current_quarter, n_managers, total_value_usd,
                            qoq_summary: {new, increased, reduced, exited, net_change_usd},
                            top_holders: [...]}
      - insiders_form4:    {since_days, n_transactions, n_insiders,
                            total_buy_usd, total_sell_usd, net_usd,
                            top_transactions: [...]}
    """
    ticker = ticker.upper()
    universe = _cedear_by_ticker()
    cedear = universe.get(ticker)
    if not cedear:
        return {"error": f"ticker {ticker} no está en CEDEAR catalog"}

    cusip = cedear["cusip"]
    db = _db()

    # ── 13F: agrupamos holdings por (filer, report_date) ──
    rows13f = list(db["Smart"]["Holdings13F"].find(
        {"cusip": cusip},
        {"_id": 0, "filer_cik": 1, "filer_name": 1, "shares": 1,
         "value_usd": 1, "filing_date": 1, "n_lines": 1},
    ))

    # Agrupo por (filer_cik, report_date) sumando shares y value.
    agg13f: dict[tuple, dict] = {}
    for r in rows13f:
        rd = _filing_date_to_report_date(r.get("filing_date"))
        if not rd:
            continue
        key = (r["filer_cik"], rd)
        slot = agg13f.setdefault(key, {
            "filer_cik": r["filer_cik"],
            "filer_name": r["filer_name"],
            "report_date": rd,
            "shares": 0,
            "value_usd": 0,
            "n_lines": 0,
        })
        slot["shares"] += r.get("shares") or 0
        slot["value_usd"] += r.get("value_usd") or 0
        slot["n_lines"] += 1

    # Encuentro el quarter más reciente con datos.
    all_quarters = sorted({s["report_date"] for s in agg13f.values()}, reverse=True)
    current_q = all_quarters[0] if all_quarters else None
    previous_q = all_quarters[1] if len(all_quarters) > 1 else None

    inst_block: dict[str, Any] = {}
    if current_q:
        curr_holders = {s["filer_cik"]: s for s in agg13f.values() if s["report_date"] == current_q}
        prev_holders = (
            {s["filer_cik"]: s for s in agg13f.values() if s["report_date"] == previous_q}
            if previous_q else {}
        )

        new_count = increased = reduced = exited = unchanged = 0
        net_change = 0
        for cik, h in curr_holders.items():
            prev_h = prev_holders.get(cik)
            if prev_h is None:
                new_count += 1
                net_change += h["value_usd"]
            elif h["shares"] > prev_h["shares"]:
                increased += 1
                net_change += h["value_usd"] - prev_h["value_usd"]
            elif h["shares"] < prev_h["shares"]:
                reduced += 1
                net_change += h["value_usd"] - prev_h["value_usd"]
            else:
                unchanged += 1
        for cik, prev_h in prev_holders.items():
            if cik not in curr_holders:
                exited += 1
                net_change -= prev_h["value_usd"]

        # Top holders del current quarter
        top = sorted(curr_holders.values(), key=lambda x: -x["value_usd"])[:20]
        top_holders_out = []
        for h in top:
            prev_h = prev_holders.get(h["filer_cik"])
            if prev_h is None:
                status = "NEW"
                delta_pct = None
            elif h["shares"] > prev_h["shares"]:
                status = "INCREASED"
                delta_pct = (
                    ((h["shares"] - prev_h["shares"]) / prev_h["shares"]) * 100
                    if prev_h["shares"] else None
                )
            elif h["shares"] < prev_h["shares"]:
                status = "REDUCED"
                delta_pct = (
                    ((h["shares"] - prev_h["shares"]) / prev_h["shares"]) * 100
                    if prev_h["shares"] else None
                )
            else:
                status = "UNCHANGED"
                delta_pct = 0.0
            top_holders_out.append({
                "filer_cik":         h["filer_cik"],
                "filer_name":        h["filer_name"],
                "shares":            h["shares"],
                "value_usd":         h["value_usd"],
                "status":            status,
                "shares_delta_pct":  delta_pct,
            })

        inst_block = {
            "current_quarter":   current_q,
            "previous_quarter":  previous_q,
            "n_managers":        len(curr_holders),
            "total_value_usd":   sum(h["value_usd"] for h in curr_holders.values()),
            "qoq_summary": {
                "new_positions":  new_count,
                "increased":      increased,
                "reduced":        reduced,
                "exited":         exited,
                "unchanged":      unchanged,
                "net_change_usd": net_change,
            },
            "top_holders":       top_holders_out,
        }

    # ── Form 4 insiders ── últimos 90 días
    cutoff_90d = (date.today() - timedelta(days=90)).isoformat()
    form4_rows = list(db["Smart"]["Form4Transactions"].find(
        {"ticker_cedear": ticker, "filing_date": {"$gte": cutoff_90d}},
        {"_id": 0},
    ).sort("filing_date", -1))

    total_buy = sum(r.get("value_usd") or 0 for r in form4_rows if r.get("tx_code") == "P")
    total_sell = sum(r.get("value_usd") or 0 for r in form4_rows if r.get("tx_code") == "S")
    insiders = {r.get("insider_name") for r in form4_rows if r.get("insider_name")}

    top_tx = sorted(
        [r for r in form4_rows if r.get("value_usd")],
        key=lambda x: -(x.get("value_usd") or 0),
    )[:10]

    insiders_block = {
        "since_days":       90,
        "since_date":       cutoff_90d,
        "n_transactions":   len(form4_rows),
        "n_insiders":       len(insiders),
        "total_buy_usd":    total_buy,
        "total_sell_usd":   total_sell,
        "net_usd":          total_buy - total_sell,
        "top_transactions": [
            {
                "insider_name":     r.get("insider_name"),
                "officer_title":    r.get("officer_title"),
                "is_director":      r.get("is_director"),
                "is_officer":       r.get("is_officer"),
                "is_ten_percent":   r.get("is_ten_percent_owner"),
                "tx_code":          r.get("tx_code"),
                "tx_type":          r.get("tx_type"),
                "shares":           r.get("shares"),
                "price_per_share":  r.get("price_per_share"),
                "value_usd":        r.get("value_usd"),
                "transaction_date": r.get("transaction_date"),
                "filing_date":      r.get("filing_date"),
            }
            for r in top_tx
        ],
    }

    return {
        "meta": {
            "ticker":       cedear["ticker"],
            "cusip":        cedear["cusip"],
            "nombre_corto": cedear["nombre_corto"],
            "cik_issuer":   cedear.get("cik_issuer"),
        },
        "institutional_13f": inst_block,
        "insiders_form4":    insiders_block,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4. Cohort overview (top buys/sells, consensus, divergences)
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=300)
def get_cohort_overview() -> dict[str, Any]:
    """Top buys/sells del cohort + consensus + divergences sobre los CEDEARs.

    Compara current_quarter vs previous_quarter agregando por ticker.
    """
    db = _db()
    universe = _cedear_universe()

    rows = list(db["Smart"]["Holdings13F"].find(
        {},
        {"_id": 0, "filer_cik": 1, "cusip": 1, "ticker_cedear": 1,
         "shares": 1, "value_usd": 1, "filing_date": 1},
    ))

    # Agrupo por (cusip, filer_cik, report_date)
    by_qcf: dict[tuple, dict] = {}
    quarters_seen: set[str] = set()
    for r in rows:
        rd = _filing_date_to_report_date(r.get("filing_date"))
        if not rd:
            continue
        quarters_seen.add(rd)
        key = (rd, r["cusip"], r["filer_cik"])
        slot = by_qcf.setdefault(key, {
            "report_date": rd, "cusip": r["cusip"], "filer_cik": r["filer_cik"],
            "ticker": r["ticker_cedear"], "shares": 0, "value_usd": 0,
        })
        slot["shares"] += r.get("shares") or 0
        slot["value_usd"] += r.get("value_usd") or 0

    quarters_sorted = sorted(quarters_seen, reverse=True)
    if len(quarters_sorted) < 1:
        return {"current_quarter": None, "top_buys": [], "top_sells": [], "consensus": [], "divergences": []}
    current_q = quarters_sorted[0]
    previous_q = quarters_sorted[1] if len(quarters_sorted) > 1 else None

    # Build per-ticker aggregations
    # by_ticker[ticker] = {"buyers": [], "sellers": [], "new": [], "exited": [], ...}
    by_ticker: dict[str, dict] = {}
    for ticker in universe.values():
        t = ticker["ticker"]
        by_ticker[t] = {
            "ticker": t, "cusip": ticker["cusip"], "nombre_corto": ticker["nombre_corto"],
            "n_holders_curr": 0, "n_holders_prev": 0,
            "new": 0, "increased": 0, "reduced": 0, "exited": 0, "unchanged": 0,
            "net_change_usd": 0, "current_value_usd": 0,
        }

    # Current quarter holders por ticker
    curr_by_ticker_filer: dict[str, dict[str, dict]] = {}
    prev_by_ticker_filer: dict[str, dict[str, dict]] = {}
    for (rd, cusip, cik), s in by_qcf.items():
        ticker_doc = universe.get(cusip)
        if not ticker_doc:
            continue
        t = ticker_doc["ticker"]
        if rd == current_q:
            curr_by_ticker_filer.setdefault(t, {})[cik] = s
        elif rd == previous_q:
            prev_by_ticker_filer.setdefault(t, {})[cik] = s

    for t in by_ticker:
        curr_holders = curr_by_ticker_filer.get(t, {})
        prev_holders = prev_by_ticker_filer.get(t, {})
        by_ticker[t]["n_holders_curr"] = len(curr_holders)
        by_ticker[t]["n_holders_prev"] = len(prev_holders)
        by_ticker[t]["current_value_usd"] = sum(s["value_usd"] for s in curr_holders.values())

        net = 0
        for cik, h in curr_holders.items():
            prev_h = prev_holders.get(cik)
            if prev_h is None:
                by_ticker[t]["new"] += 1
                net += h["value_usd"]
            elif h["shares"] > prev_h["shares"]:
                by_ticker[t]["increased"] += 1
                net += h["value_usd"] - prev_h["value_usd"]
            elif h["shares"] < prev_h["shares"]:
                by_ticker[t]["reduced"] += 1
                net += h["value_usd"] - prev_h["value_usd"]
            else:
                by_ticker[t]["unchanged"] += 1
        for cik, prev_h in prev_holders.items():
            if cik not in curr_holders:
                by_ticker[t]["exited"] += 1
                net -= prev_h["value_usd"]
        by_ticker[t]["net_change_usd"] = net

        # Buy ratio = (new + increased) / total currentmovers (excl unchanged)
        movers = by_ticker[t]["new"] + by_ticker[t]["increased"] + by_ticker[t]["reduced"] + by_ticker[t]["exited"]
        if movers:
            buys = by_ticker[t]["new"] + by_ticker[t]["increased"]
            by_ticker[t]["buy_pct"] = round((buys / movers) * 100, 1)
        else:
            by_ticker[t]["buy_pct"] = None

    tickers = list(by_ticker.values())

    top_buys = sorted(
        [t for t in tickers if t["net_change_usd"] > 0],
        key=lambda x: -x["net_change_usd"],
    )[:10]

    top_sells = sorted(
        [t for t in tickers if t["net_change_usd"] < 0],
        key=lambda x: x["net_change_usd"],
    )[:10]

    consensus_buy = sorted(
        [t for t in tickers if t["buy_pct"] and t["buy_pct"] >= 70 and t["n_holders_curr"] >= 20],
        key=lambda x: -x["buy_pct"],
    )[:10]

    consensus_sell = sorted(
        [t for t in tickers if t["buy_pct"] is not None and t["buy_pct"] <= 30 and t["n_holders_curr"] >= 20],
        key=lambda x: x["buy_pct"],
    )[:10]

    divergences = sorted(
        [
            t for t in tickers
            if t["buy_pct"] is not None and 45 <= t["buy_pct"] <= 55
            and (t["new"] + t["increased"] + t["reduced"] + t["exited"]) >= 20
        ],
        key=lambda x: -(t["new"] + t["increased"] + t["reduced"] + t["exited"]),
    )[:10]

    return {
        "current_quarter":  current_q,
        "previous_quarter": previous_q,
        "top_buys":         top_buys,
        "top_sells":        top_sells,
        "consensus_buy":    consensus_buy,
        "consensus_sell":   consensus_sell,
        "divergences":      divergences,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 5. Recent activity (últimos N días) — Form 4
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=120)
def get_recent_activity(days: int = 7) -> dict[str, Any]:
    """Últimas N días de Form 4 transactions del catálogo."""
    db = _db()
    cutoff = (date.today() - timedelta(days=days)).isoformat()

    form4 = list(db["Smart"]["Form4Transactions"].find(
        {"filing_date": {"$gte": cutoff}, "value_usd": {"$gt": 0}},
        {"_id": 0},
    ).sort("filing_date", -1).limit(50))

    return {
        "since_days":  days,
        "since_date":  cutoff,
        "ts":          datetime.now(UTC),
        "transactions": [
            {
                "ticker":          r.get("ticker_cedear"),
                "insider_name":    r.get("insider_name"),
                "officer_title":   r.get("officer_title"),
                "tx_code":         r.get("tx_code"),
                "shares":          r.get("shares"),
                "value_usd":       r.get("value_usd"),
                "transaction_date": r.get("transaction_date"),
                "filing_date":     r.get("filing_date"),
            }
            for r in form4
        ],
    }
