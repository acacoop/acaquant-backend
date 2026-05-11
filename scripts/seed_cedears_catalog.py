"""Seed inicial de Smart.CEDEARsCatalog — 35 CEDEARs más líquidos.

Para cada ticker:
  1. Lookup OpenFIGI por ticker (US) → CUSIP + nombre + exchange.
  2. Lookup en company_tickers.json de SEC → CIK del emisor.
  3. Compone el doc con `ratio` hardcodeado (mejor guess, validable).

Antes de persistir, muestra la tabla completa en consola y pide confirmación.
Si decís N, sale sin tocar nada — editás la lista de RATIOS_CEDEARS en este
script y volvés a correr.

Convención del campo `ratio`: "X:1" significa X CEDEARs locales = 1 share
del underlying. Ej. AAPL '10:1' → 10 CEDEARs en BYMA equivalen a 1 acción
de AAPL en NASDAQ.

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

# (ticker_underlying, ratio CEDEAR:underlying, nombre corto display)
# Best-guess de ratios actuales. Si alguno cambió post-split, lo editás acá
# y reseed. El BYMA es la fuente de verdad cuando hay dudas.
RATIOS_CEDEARS: list[tuple[str, str, str]] = [
    # Tech
    ("AAPL",  "10:1",  "Apple"),
    ("MSFT",  "5:1",   "Microsoft"),
    ("NVDA",  "20:1",  "Nvidia"),
    ("GOOGL", "58:1",  "Alphabet"),
    ("META",  "8:1",   "Meta Platforms"),
    ("AMZN",  "18:1",  "Amazon"),
    ("NFLX",  "30:1",  "Netflix"),
    ("AMD",   "10:1",  "AMD"),
    ("TSLA",  "15:1",  "Tesla"),
    ("INTC",  "2:1",   "Intel"),
    ("ADBE",  "10:1",  "Adobe"),
    ("CRM",   "5:1",   "Salesforce"),
    ("ORCL",  "5:1",   "Oracle"),
    ("IBM",   "2:1",   "IBM"),
    # Finance
    ("JPM",   "5:1",   "JPMorgan Chase"),
    ("BAC",   "2:1",   "Bank of America"),
    ("V",     "10:1",  "Visa"),
    ("MA",    "20:1",  "Mastercard"),
    ("GS",    "20:1",  "Goldman Sachs"),
    ("WFC",   "2:1",   "Wells Fargo"),
    # Consumer
    ("KO",    "5:1",   "Coca-Cola"),
    ("PEP",   "10:1",  "PepsiCo"),
    ("WMT",   "5:1",   "Walmart"),
    ("COST",  "30:1",  "Costco"),
    ("NKE",   "5:1",   "Nike"),
    ("DIS",   "4:1",   "Walt Disney"),
    ("MCD",   "20:1",  "McDonald's"),
    ("SBUX",  "3:1",   "Starbucks"),
    # Pharma / Health
    ("JNJ",   "5:1",   "Johnson & Johnson"),
    ("PFE",   "2:1",   "Pfizer"),
    ("UNH",   "20:1",  "UnitedHealth"),
    # Industrial / Energy
    ("BA",    "10:1",  "Boeing"),
    ("XOM",   "5:1",   "ExxonMobil"),
    ("GE",    "5:1",   "General Electric"),
    # Otros / LATAM
    ("BABA",  "5:1",   "Alibaba"),
    ("MELI",  "10:1",  "MercadoLibre"),
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
    # El payload viene indexed por string-of-int (0, 1, 2, ...) — flatten.
    for v in data.values():
        ticker = (v.get("ticker") or "").upper().strip()
        cik = str(v.get("cik_str") or 0).zfill(10)
        if ticker and cik != "0000000000":
            out[ticker] = cik
    print(f"   ✓ {len(out)} tickers cargados de SEC\n")
    return out


def _build_rows(sec_map: dict[str, str]) -> list[dict]:
    """Para cada CEDEAR, arma el doc combinando OpenFIGI + SEC map."""
    rows: list[dict] = []
    for ticker, ratio, nombre_corto in RATIOS_CEDEARS:
        print(f"   lookup {ticker:<7} ...", end="", flush=True)
        figi = lookup_ticker(ticker)
        if not figi:
            print(" ✗ OpenFIGI sin match")
            rows.append({
                "ticker":       ticker,
                "nombre_corto": nombre_corto,
                "ratio":        ratio,
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
            "ratio":               ratio,
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
        f"   {'TICKER':<7} {'RATIO':<7} {'CUSIP':<11} {'CIK':<10} "
        f"{'EXCH':<5} {'NAME':<35} {'WARN':<10}"
    )
    print("─" * 100)
    for r in rows:
        warn = "WARN" if r.get("_warning") or not r.get("cusip") or not r.get("cik_issuer") else ""
        print(
            f"   {r['ticker']:<7} "
            f"{r['ratio']:<7} "
            f"{(r.get('cusip') or '—'):<11} "
            f"{(r.get('cik_issuer') or '—'):<10} "
            f"{(r.get('exchange') or '—'):<5} "
            f"{(r.get('name') or '—')[:35]:<35} "
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
    print(f"Seed Smart.CEDEARsCatalog — {len(RATIOS_CEDEARS)} tickers\n")
    sec_map = _download_sec_ticker_map()
    print("→ resolviendo CUSIP/name/exchange via OpenFIGI (cache habilitado)…\n")
    rows = _build_rows(sec_map)
    _print_tabla(rows)
    if _confirm():
        _persistir(rows)
    else:
        print("Salgo sin persistir. Si querés ajustar ratios, editá")
        print("RATIOS_CEDEARS en este script y volvé a correr.")
        sys.exit(0)


if __name__ == "__main__":
    run()
