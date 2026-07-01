"""refinitiv_fundamentals.py — ingesta de fundamentals de Refinitiv a research.*

Standalone: NO depende del repo. Corre en TU PC con Workspace/Eikon ABIERTO.
Pegá tu APP_KEY y tu POSTGRES_URI (Supabase) abajo. Recorre RICS y, por cada empresa,
trae los ESTADOS (income/balance/cashflow, trimestral + anual) + RATIOS + SEGMENTOS y
la FOTO de mercado, y hace upsert idempotente a Supabase (schemas research.*).

Solo datos REALES — nada de estimados/consenso/futuro.

    python refinitiv_fundamentals.py --dry-run    # prueba: imprime lo que escribiría
    python refinitiv_fundamentals.py              # escribe a la base

Instalar en tu PC:  pip install eikon pandas "psycopg[binary]"
"""
import argparse
import warnings

import eikon as ek
import pandas as pd
import psycopg

warnings.simplefilter("ignore", FutureWarning)
warnings.simplefilter("ignore", RuntimeWarning)

# ── CONFIG (pegá tus valores) ──────────────────────────────────────────────
APP_KEY = "PEGA_TU_APP_KEY_ACA"
POSTGRES_URI = "PEGA_TU_POSTGRES_URI_ACA"   # el mismo connection string de Supabase

RICS = ["RKLB.O"]   # sumá más acá cuando quieras: ["RKLB.O", "AAPL.O", "MSFT.O", ...]

# ── Campos por estado (ya verificados contra RKLB) ─────────────────────────
STATEMENTS = {
    "income": [
        "TR.Revenue", "TR.CostOfRevenueTotal", "TR.GrossProfit", "TR.ResearchAndDevelopment",
        "TR.TotalOperatingExpense", "TR.OperatingIncome", "TR.EBITDA", "TR.DepreciationAmort",
        "TR.PretaxIncome", "TR.IncomeTaxes", "TR.NetIncomeAfterTaxes", "TR.EPSActValue",
    ],
    "balance": [
        "TR.CashAndSTInvestments", "TR.TotalReceivablesNet", "TR.TotalInventory",
        "TR.TotalCurrentAssets", "TR.NetPPE", "TR.TotalAssets", "TR.TotalCurrentLiabilities",
        "TR.TotalDebt", "TR.TotalLiabilities", "TR.TotalEquity",
    ],
    "cashflow": [
        "TR.CashFromOperatingActivities", "TR.CapitalExpenditures", "TR.FreeCashFlow",
        "TR.CashFromInvestingActivities", "TR.CashFromFinancingActivities",
    ],
    "ratios": [
        "TR.PE", "TR.EVToEBITDA", "TR.PriceToSalesPerShare", "TR.PriceToBVPerShare",
        "TR.ROEActValue", "TR.ROAActValue",
    ],
}
SNAPSHOT = [
    "TR.CommonName", "TR.TRBCEconomicSector", "TR.ExchangeName", "TR.CurrencyCode",
    "TR.PriceClose", "TR.Price52WeekHigh", "TR.Price52WeekLow", "TR.CompanyMarketCap",
    "TR.EnterpriseValue", "TR.SharesOutstanding", "TR.DividendYield",
]
PARAMS = {"Q": {"Period": "FQ0", "Frq": "FQ", "SDate": "0", "EDate": "-11"},   # 12 trimestres
          "FY": {"Period": "FY0", "Frq": "FY", "SDate": "0", "EDate": "-4"}}   # 5 años


def _num(v):
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else float(v)


def _fiscal(period_end, freq):
    return (f"{period_end.year} Q{(period_end.month - 1) // 3 + 1}" if freq == "Q"
            else str(period_end.year))


def estado_long(ric, statement, freq):
    """Filas para research.fundamentals desde un get_data de un estado."""
    df, _ = ek.get_data([ric], ["TR.Revenue.date", *STATEMENTS[statement]], PARAMS[freq])
    recs = []
    for _, row in df.iterrows():
        pe = pd.to_datetime(row.get("Date"))
        if pd.isna(pe):
            continue
        pe = pe.date()
        fp = _fiscal(pe, freq)
        for col in df.columns:
            if col in ("Date", "Instrument"):
                continue
            v = _num(row[col])
            if v is None:
                continue
            recs.append((ric, statement, freq, pe, fp, str(col), "", v, None))
    return recs


def segmentos_long(ric):
    """Filas para research.fundamentals (statement='segment'). Best-effort."""
    try:
        df, _ = ek.get_data(
            [ric],
            ["TR.Revenue.date", "TR.BGS.BusTotalRevenue.segmentName", "TR.BGS.BusTotalRevenue"],
            {"Period": "FQ0", "Frq": "FQ", "SDate": "0", "EDate": "-3"},
        )
    except Exception as e:
        print(f"   (segmentos {ric}: {e})")
        return []
    seg_col = next((c for c in df.columns if "segment" in c.lower()), None)
    val_col = next((c for c in df.columns if "revenue" in c.lower()), None)
    if not seg_col or not val_col:
        return []
    recs = []
    for _, row in df.iterrows():
        pe = pd.to_datetime(row.get("Date"))
        seg = row.get(seg_col)
        v = _num(row.get(val_col))
        if pd.isna(pe) or not seg or v is None:
            continue
        pe = pe.date()
        recs.append((ric, "segment", "Q", pe, _fiscal(pe, "Q"), "Revenue", str(seg), v, None))
    return recs


def snapshot_row(ric):
    """(fila market_snapshot, fila companies) desde el get_data de mercado."""
    df, _ = ek.get_data([ric], SNAPSHOT, {})
    row = df.iloc[0]

    def g(sub):
        col = next((c for c in df.columns if sub.lower() in c.lower()), None)
        return row[col] if col is not None else None

    def gn(sub):
        return _num(g(sub))

    def gs(sub):
        v = g(sub)
        return None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)

    snap = (ric, gn("Price Close"), gn("52 Week High"), gn("52 Week Low"),
            gn("Market Cap"), gn("Enterprise Value"), gn("Shares"), gn("Dividend"),
            gs("Currency"))
    comp = (ric, ric.split(".")[0], gs("Common Name"), gs("Economic Sector"),
            None, gs("Exchange"), gs("Currency"))
    return snap, comp


_F_COLS = "ric, statement, freq, period_end, fiscal_period, item, segment, value, currency"
_SQL_FUND = (
    f"INSERT INTO research.fundamentals ({_F_COLS}, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, now()) "
    "ON CONFLICT (ric, statement, freq, period_end, item, segment) "
    "DO UPDATE SET value=EXCLUDED.value, fiscal_period=EXCLUDED.fiscal_period, "
    "currency=EXCLUDED.currency, updated_at=now()"
)
_SQL_SNAP = (
    "INSERT INTO research.market_snapshot "
    "(ric, price, high_52w, low_52w, market_cap, ev, shares, div_yield, currency, updated_at) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, now()) "
    "ON CONFLICT (ric) DO UPDATE SET price=EXCLUDED.price, high_52w=EXCLUDED.high_52w, "
    "low_52w=EXCLUDED.low_52w, market_cap=EXCLUDED.market_cap, ev=EXCLUDED.ev, "
    "shares=EXCLUDED.shares, div_yield=EXCLUDED.div_yield, currency=EXCLUDED.currency, updated_at=now()"
)
_SQL_COMP = (
    "INSERT INTO research.companies (ric, ticker, nombre, sector, cedear_ticker, bolsa, moneda, updated_at) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s, now()) "
    "ON CONFLICT (ric) DO UPDATE SET ticker=EXCLUDED.ticker, nombre=EXCLUDED.nombre, "
    "sector=EXCLUDED.sector, bolsa=EXCLUDED.bolsa, moneda=EXCLUDED.moneda, updated_at=now()"
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="imprime lo que escribiría, no toca la base")
    a = ap.parse_args()
    ek.set_app_key(APP_KEY)

    conn = None if a.dry_run else psycopg.connect(POSTGRES_URI)
    for ric in RICS:
        print(f"\n=== {ric} ===")
        fund = []
        for st in STATEMENTS:
            for freq in ("Q", "FY"):
                fund += estado_long(ric, st, freq)
        fund += segmentos_long(ric)
        snap, comp = snapshot_row(ric)

        print(f"  fundamentals: {len(fund)} filas · snapshot: precio={snap[1]} mktcap={snap[4]} "
              f"· empresa={comp[2]}")
        if a.dry_run:
            for r in fund[:6]:
                print("   ej:", r[1], r[2], r[3], r[5], "=", r[7])
            continue
        with conn.cursor() as cur:
            cur.executemany(_SQL_FUND, fund)
            cur.execute(_SQL_SNAP, snap)
            cur.execute(_SQL_COMP, comp)
        conn.commit()
        print(f"  ✓ escrito ({len(fund)} fundamentals + snapshot + catálogo)")

    if conn:
        conn.close()
    print("\n(dry-run: no se escribió nada)" if a.dry_run else "\nListo.")


if __name__ == "__main__":
    main()
