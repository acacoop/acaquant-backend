"""refinitiv_prueba.py — PRUEBA del API de Eikon (RKLB.O), modelado ampliado.

Standalone: NO depende del repo. Copialo a tu PC (con Workspace/Eikon ABIERTO),
pegá tu app key abajo, y corrélo:  python refinitiv_prueba.py

SOLO imprime el response (no escribe a la base). Trae, para RKLB.O:
  1) Estados: Income / Balance / Cash Flow (8 trimestres)
  2) Márgenes (calculados: bruto / EBITDA / operativo / neto)
  3) Ratios y valuación (P/E, EV/EBITDA, P/S, P/BV, ROE, ROA)
  4) Snapshot de mercado (precio, 52w, market cap, EV, div yield, price target)
  5) Estimados de consenso (Revenue/EPS/EBITDA/Net Income a futuro)
  6) Segmentos (ingresos por línea de negocio)

Nota: en RKLB varios ratios (P/E, EV/EBITDA, ROA) vienen <NA> PORQUE la empresa da
pérdidas (no existen matemáticamente); en una empresa rentable van a traer valor.

Requisitos en tu PC:
    pip install eikon pandas   (+ Workspace/Eikon abierto y logueado)
"""
import warnings

import eikon as ek
import pandas as pd

warnings.simplefilter("ignore", FutureWarning)   # avisos internos de eikon/pandas
warnings.simplefilter("ignore", RuntimeWarning)
pd.set_option("display.width", 260)
pd.set_option("display.max_columns", 60)

# ── 1) API Key ─────────────────────────────────────────────────────────────
ek.set_app_key("PEGA_TU_APP_KEY_ACA")

RIC = "RKLB.O"
Q = {"Period": "FQ0", "Frq": "FQ", "SDate": "0", "EDate": "-7"}   # 8 trimestres
FY = {"Period": "FY0", "Frq": "FY", "SDate": "0", "EDate": "2"}   # actual + 2 años futuros

INCOME = [
    "TR.Revenue", "TR.CostOfRevenueTotal", "TR.GrossProfit", "TR.ResearchAndDevelopment",
    "TR.TotalOperatingExpense", "TR.OperatingIncome", "TR.EBITDA",
    "TR.DepreciationAmort", "TR.PretaxIncome", "TR.IncomeTaxes",
    "TR.NetIncomeAfterTaxes", "TR.EPSActValue",
]
BALANCE = [
    "TR.CashAndSTInvestments", "TR.TotalReceivablesNet", "TR.TotalInventory",
    "TR.TotalCurrentAssets", "TR.NetPPE", "TR.TotalAssets",
    "TR.TotalCurrentLiabilities", "TR.TotalDebt", "TR.TotalLiabilities", "TR.TotalEquity",
]
CASHFLOW = [
    "TR.CashFromOperatingActivities", "TR.CapitalExpenditures", "TR.FreeCashFlow",
    "TR.CashFromInvestingActivities", "TR.CashFromFinancingActivities",
]
RATIOS = [
    "TR.PE", "TR.EVToEBITDA", "TR.PriceToSalesPerShare", "TR.PriceToBVPerShare",
    "TR.ROEActValue", "TR.ROAActValue",
]
SNAPSHOT = [
    "TR.PriceClose", "TR.Price52WeekHigh", "TR.Price52WeekLow", "TR.CompanyMarketCap",
    "TR.EnterpriseValue", "TR.SharesOutstanding", "TR.DividendYield",
    "TR.PriceTargetMean", "TR.RecommendationMean",
]
ESTIMATES = [
    "TR.RevenueMeanEstimate", "TR.EPSMeanEstimate", "TR.EBITDAMean", "TR.NetIncomeMeanEstimate",
]


def _periodo(df: pd.DataFrame) -> pd.DataFrame:
    """No muta: agrega 'Periodo' (Año + Trimestre) desde la fecha y limpia columnas."""
    df = df.copy()
    if "Date" not in df.columns:
        return df.drop(columns=["Instrument"], errors="ignore")
    d = pd.to_datetime(df["Date"])
    df.insert(0, "Periodo", d.dt.year.astype(str) + " Q" + (((d.dt.month - 1) // 3) + 1).astype(str))
    return df.drop(columns=["Date", "Instrument"], errors="ignore")


def traer(nombre, campos, params, period="TR.Revenue.date"):
    fields = ([period] if period else []) + campos
    df, err = ek.get_data([RIC], fields, params or {})
    out = _periodo(df) if period == "TR.Revenue.date" else df.drop(columns=["Instrument"], errors="ignore")
    print(f"\n==================  {nombre}  ({RIC})  ==================")
    print(out.to_string(index=False))
    if err:
        print(f"--- avisos {nombre} (campos que no resolvieron):")
        for e in err:
            print("   ", e)
    return out


# ── Estados ────────────────────────────────────────────────────────────────
inc = traer("INCOME STATEMENT", INCOME, Q)
traer("BALANCE SHEET", BALANCE, Q)
traer("CASH FLOW", CASHFLOW, Q)

# ── Márgenes (calculados desde el income; inc ya trae la columna Periodo) ───
try:
    rev = inc["Revenue"]
    mar = pd.DataFrame({
        "Periodo": inc["Periodo"],
        "Margen Bruto %": (inc["Gross Profit"] / rev * 100).round(1),
        "Margen EBITDA %": (inc["EBITDA"] / rev * 100).round(1),
        "Margen Operativo %": (inc["Operating Income"] / rev * 100).round(1),
        "Margen Neto %": (inc["Net Income After Taxes"] / rev * 100).round(1),
    })
    print("\n==================  MÁRGENES (calculados)  ==================")
    print(mar.to_string(index=False))
except Exception as e:
    print(f"\n(no pude calcular márgenes: {e})")

# ── Ratios / snapshot / estimados ──────────────────────────────────────────
traer("RATIOS / VALUACIÓN", RATIOS, Q)
traer("SNAPSHOT DE MERCADO", SNAPSHOT, None, period=None)
traer("ESTIMADOS (CONSENSO)", ESTIMATES, FY, period="TR.RevenueMeanEstimate.fperiod")

# ── Segmentos (una fila por segmento) ──────────────────────────────────────
seg, serr = ek.get_data(
    [RIC],
    ["TR.BGS.BusTotalRevenue.segmentName", "TR.BGS.BusTotalRevenue"],
    {"Period": "FQ0", "Frq": "FQ", "SDate": "0", "EDate": "-3"},
)
print(f"\n==================  SEGMENTOS (ingresos)  ({RIC})  ==================")
print(seg.drop(columns=["Instrument"], errors="ignore").to_string(index=False))
if serr:
    print("--- avisos segmentos:")
    for e in serr:
        print("   ", e)

print("\nValores en unidades (÷1.000.000 = millones). Pegame la salida y cerramos "
      "qué dejamos; después lo modelamos y conectamos a la base.")
