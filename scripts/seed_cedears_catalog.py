"""Seed inicial de Smart.CEDEARsCatalog — 35 US tickers operables en BYMA.

El catálogo es un **filtro maestro**: define qué CUSIPs nos importan al
agregar signals de 13F / Form 4 / Congress. Si un manager reporta un
holding cuyo CUSIP no está acá, la vista lo ignora.

NO guardamos ratios CEDEAR:underlying. No estamos haciendo arbitraje;
solo queremos saber "¿este ticker se puede comprar localmente?".

Para cada ticker:
  1. Lookup OpenFIGI → CUSIP + nombre + exchange.
  2. Lookup en company_tickers.json de SEC → CIK del emisor.
  3. Compone el doc.

Antes de persistir, muestra la tabla en consola y pide confirmación.
Si N, sale sin tocar nada — editás la lista TICKERS_CEDEARS y rerun.

Idempotente: upsert por ticker.

Uso:
    python -m scripts.seed_cedears_catalog
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime

import requests

from core.mongo import get_mongo_client
from core.openfigi import lookup_ticker
from core.sec_edgar import USER_AGENT

# (ticker_underlying, nombre corto para display).
# Los 35 más líquidos que operan como CEDEAR en BYMA. Si querés agregar
# o sacar, editá esta lista y volvé a correr.
TICKERS_CEDEARS: list[tuple[str, str]] = [
    # Tech
    ("AAPL",  "Apple"),
    ("MSFT",  "Microsoft"),
    ("NVDA",  "Nvidia"),
    ("GOOGL", "Alphabet"),
    ("META",  "Meta Platforms"),
    ("AMZN",  "Amazon"),
    ("NFLX",  "Netflix"),
    ("AMD",   "AMD"),
    ("TSLA",  "Tesla"),
    ("INTC",  "Intel"),
    ("ADBE",  "Adobe"),
    ("CRM",   "Salesforce"),
    ("ORCL",  "Oracle"),
    ("IBM",   "IBM"),
    # Finance
    ("JPM",   "JPMorgan Chase"),
    ("BAC",   "Bank of America"),
    ("V",     "Visa"),
    ("MA",    "Mastercard"),
    ("GS",    "Goldman Sachs"),
    ("WFC",   "Wells Fargo"),
    # Consumer
    ("KO",    "Coca-Cola"),
    ("PEP",   "PepsiCo"),
    ("WMT",   "Walmart"),
    ("COST",  "Costco"),
    ("NKE",   "Nike"),
    ("DIS",   "Walt Disney"),
    ("MCD",   "McDonald's"),
    ("SBUX",  "Starbucks"),
    # Pharma / Health
    ("JNJ",   "Johnson & Johnson"),
    ("PFE",   "Pfizer"),
    ("UNH",   "UnitedHealth"),
    # Industrial / Energy
    ("BA",    "Boeing"),
    ("XOM",   "ExxonMobil"),
    ("GE",    "General Electric"),
    # Otros / LATAM
    ("BABA",  "Alibaba"),
    ("MELI",  "MercadoLibre"),
]

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def _download_sec_ticker_map() -> dict[str, str]:
    """SEC publica un JSON con todos los tickers US + sus CIK. Lo bajamos
    una vez y devolvemos un dict ticker→cik_padded.
    """
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
    print(f"   ✓ {len(out)} tickers cargados de SEC\n")
    return out


def _build_rows(sec_map: dict[str, str]) -> list[dict]:
    rows: list[dict] = []
    for ticker, nombre_corto in TICKERS_CEDEARS:
        print(f"   lookup {ticker:<7} ...", end="", flush=True)
        figi = lookup_ticker(ticker)
        if not figi:
            print(" ✗ OpenFIGI sin match")
            rows.append({
                "ticker":       ticker,
                "nombre_corto": nombre_corto,
                "cusip":        None,
                "name":         None,
                "exchange":     None,
                "cik_issuer":   sec_map.get(ticker),
                "is_active":    True,
                "_warning":     "OpenFIGI no devolvió match",
            })
            continue
        cik_issuer = sec_map.get(ticker)
        rows.append({
            "ticker":              ticker,
            "nombre_corto":        nombre_corto,
            "cusip":               figi.get("cusip"),
            "name":                figi.get("name"),
            "exchange":            figi.get("exchange"),
            "security_type":       figi.get("security_type"),
            "cik_issuer":          cik_issuer,
            "is_active":           True,
        })
        warn = "" if cik_issuer else "  ⚠ SEC sin CIK"
        print(f" ✓ cusip={figi.get('cusip')}  cik={cik_issuer or '—'}{warn}")
    return rows


def _print_tabla(rows: list[dict]) -> None:
    print("\n" + "─" * 100)
    print(
        f"   {'TICKER':<7} {'CUSIP':<11} {'CIK':<10} "
        f"{'EXCH':<5} {'NAME':<40} {'WARN':<10}"
    )
    print("─" * 100)
    for r in rows:
        warn = "WARN" if r.get("_warning") or not r.get("cusip") or not r.get("cik_issuer") else ""
        print(
            f"   {r['ticker']:<7} "
            f"{(r.get('cusip') or '—'):<11} "
            f"{(r.get('cik_issuer') or '—'):<10} "
            f"{(r.get('exchange') or '—'):<5} "
            f"{(r.get('name') or '—')[:40]:<40} "
            f"{warn:<10}"
        )
    print("─" * 100)
    n_ok = sum(1 for r in rows if r.get("cusip") and r.get("cik_issuer"))
    print(f"   {n_ok}/{len(rows)} filas completas (CUSIP + CIK)")


def _confirm() -> bool:
    print("\nPersistir esta tabla en Smart.CEDEARsCatalog? (y/N): ", end="", flush=True)
    try:
        ans = input().strip().lower()
    except EOFError:
        return False
    return ans == "y"


def _persistir(rows: list[dict]) -> None:
    db = get_mongo_client()
    col = db["Smart"]["CEDEARsCatalog"]
    ts = datetime.now(UTC)
    n = 0
    for r in rows:
        doc = {k: v for k, v in r.items() if not k.startswith("_")}
        doc["seeded_at"] = ts
        col.update_one({"ticker": r["ticker"]}, {"$set": doc}, upsert=True)
        n += 1
    total = col.count_documents({})
    print(f"\n✓ {n} CEDEARs upserted. Smart.CEDEARsCatalog tiene ahora {total} docs.")


def run() -> None:
    print(f"Seed Smart.CEDEARsCatalog — {len(TICKERS_CEDEARS)} tickers\n")
    sec_map = _download_sec_ticker_map()
    print("→ resolviendo CUSIP/name/exchange via OpenFIGI (cache habilitado)…\n")
    rows = _build_rows(sec_map)
    _print_tabla(rows)
    if _confirm():
        _persistir(rows)
    else:
        print("Salgo sin persistir. Si querés ajustar la lista, editá")
        print("TICKERS_CEDEARS en este script y volvé a correr.")
        sys.exit(0)


if __name__ == "__main__":
    run()
