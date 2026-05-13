"""seed_cedears.py — upsert de CEDEARs en Trading.Cedears.

Colección master con metadata categórica (sector, industria, región, país).
Espejo conceptual de Trading.Curvas pero para acciones via CEDEAR.

NO toca market data — eso vive aparte en `Trading.CedearsSnapshot` (escrito
por `engines/motor_cedears.py` cada 1s).

LAR y URAC quedan con sector=OTROS / industria=TODO porque no los puedo
identificar con certeza desde acá. Editá el doc en Mongo cuando los
confirmes para que aparezcan bien categorizados en el scanner.

Uso:
    python -m scripts.seed_cedears               # upsert todos
    python -m scripts.seed_cedears --dry-run     # solo print, no escribe
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from core.mongo import get_mongo_client


NOMBRES: dict[str, str] = {
    # US Tech mega-caps
    "AAPL":  "Apple",
    "MSFT":  "Microsoft",
    "GOOGL": "Alphabet (Google)",
    "META":  "Meta Platforms",
    "AMZN":  "Amazon",
    "NFLX":  "Netflix",
    "ORCL":  "Oracle",
    "PLTR":  "Palantir Technologies",
    "UBER":  "Uber Technologies",
    "PANW":  "Palo Alto Networks",
    "RIOT":  "Riot Platforms",
    # Semis
    "NVDA":  "NVIDIA",
    "AMD":   "Advanced Micro Devices",
    "INTC":  "Intel",
    "TSM":   "Taiwan Semiconductor",
    "QCOM":  "Qualcomm",
    "MU":    "Micron Technology",
    "ARM":   "Arm Holdings",
    "ASML":  "ASML Holding",
    # Financiero
    "JPM":   "JPMorgan Chase",
    "V":     "Visa",
    "MA":    "Mastercard",
    "C":     "Citigroup",
    "GS":    "Goldman Sachs",
    # Salud
    "JNJ":   "Johnson & Johnson",
    "PFE":   "Pfizer",
    # Industriales / Aero & Defensa
    "LMT":   "Lockheed Martin",
    "GE":    "GE Aerospace",
    "RKLB":  "Rocket Lab",
    # Consumo
    "KO":    "Coca-Cola",
    "PEP":   "PepsiCo",
    "WMT":   "Walmart",
    "NKE":   "Nike",
    "DIS":   "Walt Disney",
    "BABA":  "Alibaba",
    "MELI":  "Mercado Libre",
    "TSLA":  "Tesla",
    # Energía
    "XOM":   "ExxonMobil",
    "CVX":   "Chevron",
    "YPFD":  "YPF",
    "VIST":  "Vista Energy",
    "PBR":   "Petrobras",
    "PAMP":  "Pampa Energía",
    "CEPU":  "Central Puerto",
    "EDN":   "Edenor",
    "TGS":   "Transportadora de Gas del Sur",
    # Bancos argentinos
    "GGAL":  "Grupo Financiero Galicia",
    "BMA":   "Banco Macro",
    "SUPV":  "Grupo Supervielle",
    # ETFs
    "SPY":   "S&P 500",
    "QQQ":   "Nasdaq 100",
    "IWM":   "Russell 2000",
    "IBIT":  "Bitcoin ETF",
    "ETHA":  "Ether ETF",
    "URA":   "Uranium ETF",
    "USO":   "US Oil Fund",
    "GLD":   "Gold Trust (Oro)",
    "XLK":   "Sector Tech SPDR",
    "XLF":   "Sector Financiero SPDR",
    "XLV":   "Sector Salud SPDR",
    "XLP":   "Sector Consumo Básico SPDR",
    "XLC":   "Sector Comunicaciones SPDR",
    "XLRE":  "Sector Real Estate SPDR",
    "ITA":   "Aero & Defensa (iShares)",
    "IBB":   "Biotech (iShares)",
    "IVW":   "S&P 500 Growth (iShares)",
    "FXI":   "China Large-Cap (iShares)",
    "ILF":   "LATAM 40 (iShares)",
    "PSQ":   "Nasdaq Short (ProShares)",
    "VXX":   "VIX Short-Term Futures",
    # Sin clasificar todavía
    "LAR":   "LAR (TODO)",
}


def _cedear(
    ticker_corto: str,
    sector: str,
    industria: str,
    region: str,
    pais: str,
    underlying: str | None = None,
) -> dict:
    """`ticker_corto` = símbolo BYMA local (ej. YPFD para CEDEAR de YPF).
    `underlying`     = símbolo del activo subyacente US (ej. YPF en NYSE).
    Si no se pasa, defaultea a ticker_corto (caso común — para la mayoría
    de los tickers BYMA y US tienen el mismo nombre).
    `nombre`         = nombre humano-friendly de la empresa/ETF, sale del
    dict NOMBRES por ticker_corto. Fallback al ticker si no está mapeado."""
    return {
        "ticker":       f"MERV - XMEV - {ticker_corto} - 24hs",
        "ticker_corto": ticker_corto,
        "underlying":   underlying or ticker_corto,
        "nombre":       NOMBRES.get(ticker_corto, ticker_corto),
        "ratio_cedear": None,  # vos lo completás post-seed
        "sector":       sector,
        "industria":    industria,
        "region":       region,
        "pais":         pais,
        "activo":       True,
    }


CEDEARS = [
    # Semiconductores (US + Asia)
    _cedear("AMD",   "TECNOLOGIA",          "Semiconductores", "US",    "USA"),
    _cedear("NVDA",  "TECNOLOGIA",          "Semiconductores", "US",    "USA"),
    _cedear("INTC",  "TECNOLOGIA",          "Semiconductores", "US",    "USA"),
    _cedear("TSM",   "TECNOLOGIA",          "Semiconductores", "ASIA",  "Taiwan"),

    # Software / Internet / Hardware (US mega-caps)
    _cedear("AAPL",  "TECNOLOGIA",          "Hardware",        "US",    "USA"),
    _cedear("MSFT",  "TECNOLOGIA",          "Software",        "US",    "USA"),
    _cedear("GOOGL", "TECNOLOGIA",          "Internet",        "US",    "USA"),
    _cedear("META",  "TECNOLOGIA",          "Internet",        "US",    "USA"),
    _cedear("AMZN",  "TECNOLOGIA",          "E-commerce",      "US",    "USA"),
    _cedear("ORCL",  "TECNOLOGIA",          "Software",        "US",    "USA"),
    _cedear("PLTR",  "TECNOLOGIA",          "Software",        "US",    "USA"),
    # DELL: BYMA no tiene CEDEAR de Dell (confirmado por user 2026-05-13).
    # Removido del scanner; la data ADR queda huérfana en PreciosAcciones.

    # Comunicaciones / streaming
    _cedear("NFLX",  "COMUNICACIONES",      "Streaming",       "US",    "USA"),

    # Consumo
    _cedear("KO",    "CONSUMO BASICO",      "Bebidas",         "US",    "USA"),
    _cedear("BABA",  "CONSUMO DISCRECIONAL","E-commerce",      "ASIA",  "China"),

    # Industriales
    _cedear("RKLB",  "INDUSTRIALES",        "Aeroespacial",    "US",    "USA"),

    # Energía (Argentina + Brasil)
    # YPF: el CEDEAR BYMA se llama YPFD (sobre el ADR US YPF). Por eso
    # ticker_corto difiere de underlying.
    _cedear("YPFD",  "ENERGIA",             "Oil & Gas",       "LATAM", "Argentina", underlying="YPF"),
    _cedear("VIST",  "ENERGIA",             "Oil & Gas",       "LATAM", "Argentina"),
    _cedear("PBR",   "ENERGIA",             "Oil & Gas",       "LATAM", "Brasil"),

    # Financiero
    _cedear("GGAL",  "FINANCIERO",          "Bancos",          "LATAM", "Argentina"),

    # ETFs (índices + cripto)
    _cedear("SPY",   "ETF",                 "S&P 500",         "US",    "USA"),
    _cedear("QQQ",   "ETF",                 "Nasdaq 100",      "US",    "USA"),
    _cedear("IWM",   "ETF",                 "Russell 2000",    "US",    "USA"),
    _cedear("IBIT",  "ETF",                 "Bitcoin",         "US",    "USA"),
    _cedear("ETHA",  "ETF",                 "Ethereum",        "US",    "USA"),

    # Pendientes de identificar — TODO: confirmar
    _cedear("LAR",   "OTROS",               "TODO",            "TODO",  "TODO"),
    # URA = Global X Uranium ETF.
    _cedear("URA",   "ETF",                 "Uranio",          "US",    "USA"),

    # Migrados desde la watchlist (sección "Acciones" que se elimina, todo
    # se concentra en el Scanner). 2026-05-13.
    _cedear("TSLA",  "CONSUMO DISCRECIONAL", "Autos",          "US",    "USA"),
    _cedear("JPM",   "FINANCIERO",           "Bancos",         "US",    "USA"),
    _cedear("MELI",  "TECNOLOGIA",           "E-commerce",     "LATAM", "Argentina"),

    # ── Expansión universo Scanner 2026-05-13 ──────────────────────────
    # Semis adicionales
    _cedear("QCOM",  "TECNOLOGIA",          "Semiconductores", "US",    "USA"),
    _cedear("MU",    "TECNOLOGIA",          "Semiconductores", "US",    "USA"),
    _cedear("ARM",   "TECNOLOGIA",          "Semiconductores", "US",    "USA"),  # Arm Holdings ADR
    _cedear("ASML",  "TECNOLOGIA",          "Semiconductores", "EU",    "Netherlands"),

    # Tech / cyber / cripto mining
    _cedear("UBER",  "TECNOLOGIA",          "Transporte",      "US",    "USA"),
    _cedear("PANW",  "TECNOLOGIA",          "Cybersecurity",   "US",    "USA"),
    _cedear("RIOT",  "TECNOLOGIA",          "Cripto Mining",   "US",    "USA"),

    # Financiero
    _cedear("V",     "FINANCIERO",          "Pagos",           "US",    "USA"),
    _cedear("MA",    "FINANCIERO",          "Pagos",           "US",    "USA"),
    _cedear("C",     "FINANCIERO",          "Bancos",          "US",    "USA"),
    _cedear("GS",    "FINANCIERO",          "Banca Inversión", "US",    "USA"),

    # Salud / Pharma
    _cedear("JNJ",   "SALUD",               "Pharma",          "US",    "USA"),
    _cedear("PFE",   "SALUD",               "Pharma",          "US",    "USA"),

    # Industriales / Defensa
    _cedear("LMT",   "INDUSTRIALES",        "Defensa",         "US",    "USA"),
    _cedear("GE",    "INDUSTRIALES",        "Aeroespacial",    "US",    "USA"),

    # Consumo
    _cedear("PEP",   "CONSUMO BASICO",      "Bebidas",         "US",    "USA"),
    _cedear("WMT",   "CONSUMO BASICO",      "Retail",          "US",    "USA"),
    _cedear("NKE",   "CONSUMO DISCRECIONAL","Apparel",         "US",    "USA"),
    _cedear("DIS",   "COMUNICACIONES",      "Entretenimiento", "US",    "USA"),

    # Energía
    _cedear("XOM",   "ENERGIA",             "Oil & Gas",       "US",    "USA"),
    _cedear("CVX",   "ENERGIA",             "Oil & Gas",       "US",    "USA"),

    # ADRs argentinos (2026-05-13). PAMP es el ticker BYMA, PAM el ADR US.
    _cedear("PAMP",  "ENERGIA",             "Eléctrica",       "LATAM", "Argentina", underlying="PAM"),
    _cedear("CEPU",  "ENERGIA",             "Eléctrica",       "LATAM", "Argentina"),
    _cedear("EDN",   "ENERGIA",             "Eléctrica",       "LATAM", "Argentina"),
    _cedear("TGS",   "ENERGIA",             "Gas",             "LATAM", "Argentina"),
    _cedear("BMA",   "FINANCIERO",          "Bancos",          "LATAM", "Argentina"),
    _cedear("SUPV",  "FINANCIERO",          "Bancos",          "LATAM", "Argentina"),

    # ETFs (sector, country, theme)
    _cedear("XLK",   "ETF",                 "Sector Tech",        "US", "USA"),
    _cedear("XLF",   "ETF",                 "Sector Financiero",  "US", "USA"),
    _cedear("XLV",   "ETF",                 "Sector Salud",       "US", "USA"),
    _cedear("XLP",   "ETF",                 "Sector Cons. Básico","US", "USA"),
    _cedear("XLC",   "ETF",                 "Sector Comunic.",    "US", "USA"),
    _cedear("XLRE",  "ETF",                 "Sector Real Estate", "US", "USA"),
    _cedear("ITA",   "ETF",                 "Aero & Defensa",     "US", "USA"),
    _cedear("IBB",   "ETF",                 "Biotech",            "US", "USA"),
    _cedear("IVW",   "ETF",                 "S&P 500 Growth",     "US", "USA"),
    _cedear("FXI",   "ETF",                 "China Large-Cap",    "ASIA","China"),
    _cedear("ILF",   "ETF",                 "LATAM 40",           "LATAM","USA"),
    _cedear("GLD",   "ETF",                 "Oro",                "US", "USA"),
    _cedear("USO",   "ETF",                 "Petróleo",           "US", "USA"),
    _cedear("PSQ",   "ETF",                 "Nasdaq Inverso",     "US", "USA"),
    _cedear("VXX",   "ETF",                 "VIX Volatilidad",    "US", "USA"),
]


def run(dry_run: bool = False) -> None:
    print("=" * 80)
    print(f"SEED Trading.Cedears — {len(CEDEARS)} docs")
    print(f"Modo: {'DRY-RUN (no escribe)' if dry_run else 'WRITE'}")
    print("=" * 80)

    if dry_run:
        for doc in CEDEARS:
            print(f"\n[DRY] upsert {doc['ticker_corto']:6s} ({doc['sector']} / {doc['industria']} / {doc['pais']})")
        return

    client = get_mongo_client()
    col = client["Trading"]["Cedears"]
    now = datetime.now(timezone.utc)

    inserted = updated = unchanged = 0
    for doc in CEDEARS:
        result = col.update_one(
            {"ticker_corto": doc["ticker_corto"]},
            {
                "$set":         doc,
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        if result.upserted_id:
            print(f"  + INSERTED {doc['ticker_corto']:6s}")
            inserted += 1
        elif result.modified_count:
            print(f"  ~ UPDATED  {doc['ticker_corto']:6s}")
            updated += 1
        else:
            unchanged += 1

    total = col.count_documents({})
    print(f"\nResumen: {inserted} insert · {updated} update · {unchanged} sin cambios")
    print(f"Total docs en Trading.Cedears: {total}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="No escribe a Mongo, solo print")
    args = ap.parse_args()
    run(dry_run=args.dry_run)
