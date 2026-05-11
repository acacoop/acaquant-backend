"""Seed inicial de Smart.CEDEARsCatalog — 36 US tickers operables en BYMA.

El catálogo es el **filtro maestro**: define qué CUSIPs nos importan al
agregar signals de 13F / Form 4. Si un manager reporta un holding cuyo
CUSIP no está acá, la vista lo ignora.

NO guardamos ratios CEDEAR:underlying. No estamos haciendo arbitraje;
solo queremos "¿este ticker se puede comprar localmente?".

Para cada ticker:
  - CUSIP: hardcodeado (public knowledge, estable durante años; el campo
    no cambia salvo restructuras corporativas tipo GE Vernova spinoff).
  - CIK del emisor: SEC company_tickers.json (descarga 1x).
  - Nombre largo + exchange: SEC map (cuando lo tiene).

Antes de persistir, muestra la tabla y pide confirmación. Si N, sale sin
tocar nada.

Idempotente: upsert por ticker.

Uso:
    python -m scripts.seed_cedears_catalog
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime

import requests

from core.mongo import get_mongo_client
from core.sec_edgar import USER_AGENT

# (ticker, cusip, nombre corto display).
# CUSIPs verificados de fuentes públicas (SEC filings + Bloomberg + Quandl).
# Son estables salvo restructuras (GE post-Vernova spinoff = 369604301 sigue
# vigente para GE Aerospace, la matriz; antes era de "General Electric Co").
TICKERS_CEDEARS: list[tuple[str, str, str]] = [
    # Tech
    ("AAPL",  "037833100", "Apple"),
    ("MSFT",  "594918104", "Microsoft"),
    ("NVDA",  "67066G104", "Nvidia"),
    ("GOOGL", "02079K305", "Alphabet (Class A)"),
    ("META",  "30303M102", "Meta Platforms"),
    ("AMZN",  "023135106", "Amazon"),
    ("NFLX",  "64110L106", "Netflix"),
    ("AMD",   "007903107", "AMD"),
    ("TSLA",  "88160R101", "Tesla"),
    ("INTC",  "458140100", "Intel"),
    ("ADBE",  "00724F101", "Adobe"),
    ("CRM",   "79466L302", "Salesforce"),
    ("ORCL",  "68389X105", "Oracle"),
    ("IBM",   "459200101", "IBM"),
    # Finance
    ("JPM",   "46625H100", "JPMorgan Chase"),
    ("BAC",   "060505104", "Bank of America"),
    ("V",     "92826C839", "Visa"),
    ("MA",    "57636Q104", "Mastercard"),
    ("GS",    "38141G104", "Goldman Sachs"),
    ("WFC",   "949746101", "Wells Fargo"),
    # Consumer
    ("KO",    "191216100", "Coca-Cola"),
    ("PEP",   "713448108", "PepsiCo"),
    ("WMT",   "931142103", "Walmart"),
    ("COST",  "22160K105", "Costco"),
    ("NKE",   "654106103", "Nike"),
    ("DIS",   "254687106", "Walt Disney"),
    ("MCD",   "580135101", "McDonald's"),
    ("SBUX",  "855244109", "Starbucks"),
    # Pharma / Health
    ("JNJ",   "478160104", "Johnson & Johnson"),
    ("PFE",   "717081103", "Pfizer"),
    ("UNH",   "91324P102", "UnitedHealth"),
    # Industrial / Energy
    ("BA",    "097023105", "Boeing"),
    ("XOM",   "30231G102", "ExxonMobil"),
    ("GE",    "369604301", "GE Aerospace"),
    # Otros / LATAM
    ("BABA",  "01609W102", "Alibaba"),
    ("MELI",  "58733R102", "MercadoLibre"),
]

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def _download_sec_ticker_map() -> dict[str, str]:
    """Devuelve ticker → CIK (zero-padded a 10 dígitos)."""
    print("→ descargando SEC company_tickers.json …")
    r = requests.get(
        SEC_TICKERS_URL,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    out: dict[str, str] = {}
    for v in data.values():
        ticker = (v.get("ticker") or "").upper().strip()
        cik = str(v.get("cik_str") or 0).zfill(10)
        if ticker and cik != "0000000000":
            out[ticker] = cik
    print(f"   ✓ {len(out)} tickers en SEC map\n")
    return out


def _build_rows(sec_map: dict[str, str]) -> list[dict]:
    rows: list[dict] = []
    for ticker, cusip, nombre_corto in TICKERS_CEDEARS:
        cik_issuer = sec_map.get(ticker)
        rows.append({
            "ticker":       ticker,
            "cusip":        cusip,
            "nombre_corto": nombre_corto,
            "cik_issuer":   cik_issuer,
            "is_active":    True,
        })
    return rows


def _print_tabla(rows: list[dict]) -> None:
    print("─" * 75)
    print(f"   {'TICKER':<7} {'CUSIP':<11} {'CIK':<10} {'NOMBRE':<30} {'WARN':<7}")
    print("─" * 75)
    for r in rows:
        warn = "" if r.get("cik_issuer") else "NO CIK"
        print(
            f"   {r['ticker']:<7} "
            f"{r['cusip']:<11} "
            f"{(r.get('cik_issuer') or '—'):<10} "
            f"{r['nombre_corto']:<30} "
            f"{warn:<7}"
        )
    print("─" * 75)
    n_ok = sum(1 for r in rows if r.get("cik_issuer"))
    print(f"   {n_ok}/{len(rows)} con CIK del emisor (necesario para Form 4)")


def _confirm() -> bool:
    print("\nPersistir en Smart.CEDEARsCatalog? (y/N): ", end="", flush=True)
    try:
        ans = input().strip().lower()
    except EOFError:
        return False
    return ans == "y"


def _persistir(rows: list[dict]) -> None:
    db = get_mongo_client()
    col = db["Smart"]["CEDEARsCatalog"]
    ts = datetime.now(UTC)
    for r in rows:
        doc = dict(r)
        doc["seeded_at"] = ts
        col.update_one({"ticker": r["ticker"]}, {"$set": doc}, upsert=True)
    total = col.count_documents({})
    print(f"\n✓ {len(rows)} CEDEARs upserted. Smart.CEDEARsCatalog ahora tiene {total} docs.")


def run() -> None:
    print(f"Seed Smart.CEDEARsCatalog — {len(TICKERS_CEDEARS)} tickers\n")
    sec_map = _download_sec_ticker_map()
    rows = _build_rows(sec_map)
    _print_tabla(rows)
    if _confirm():
        _persistir(rows)
    else:
        print("Salgo sin persistir. Editá TICKERS_CEDEARS y volvé a correr si querés cambiar la lista.")
        sys.exit(0)


if __name__ == "__main__":
    run()
