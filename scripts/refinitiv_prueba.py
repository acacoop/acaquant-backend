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

Campos con  # ?  = tentativos (si fallan, get_data los lista en "avisos"; los corrijo).
Los de estimados salen del "Tablero Acciones" (verificados). Requisitos en tu PC:
    pip install eikon pandas   (+ Workspace/Eikon abierto y logueado)
"""
import warnings

import eikon as ek
import pandas as pd

warnings.simplefilter("ignore", FutureWarning)  # silencia el aviso interno de eikon
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
    "TR.PE",                  # ? P / E
    "TR.EVToEBITDA",          # ? EV / EBITDA
    "TR.PriceToSalesPerShare",  # ? Price / Sales
    "TR.PriceToBVPerShare",   # ? Price / Book
    "TR.ROEActValue",         # ? ROE
    "TR.ROAActValue",         # ? ROA
]
SNAPSHOT = [
    "TR.PriceClose",          # ? último precio
    "TR.Price52WeekHigh",     # ? máx 52 sem
    "TR.Price52WeekLow",      # ? mín 52 sem
    "TR.CompanyMarketCap",    # cap de mercado
    "TR.EnterpriseValue",     # ? EV
    "TR.SharesOutstanding",   # ? acciones en circ.
    "TR.DividendYield",       # ? dividend yield
    "TR.PriceTargetMean",     # ? precio objetivo (consenso)
    "TR.RecommendationLabel", # ? recomendación
]
ESTIMATES = [
    "TR.RevenueMeanEstimate",    # ingresos estimados (consenso)
    "TR.EPSMeanEstimate",        # EPS estimado
    "TR.EBITDAMean",             # EBITDA estimado
    "TR.NetIncomeMeanEstimate",  # resultado neto estimado
]


def _periodo(df: pd.DataFrame) -> pd.DataFrame:
    if "Date" not in df.columns:
        return df.drop(columns=["Instrument"], errors="ignore")
    d = pd.to_datetime(df["Date"])
    df.insert(0, "Periodo", d.dt.year.astype(str) + " Q" + (((d.dt.month - 1) // 3) + 1).astype(str))
    return df.drop(columns=["Date", "Instrument"], errors="ignore")


def traer(nombre, campos, params, period="TR.Revenue.date"):
    fields = ([period] if period else []) + campos
    df, err = ek.get_data([RIC], fields, params or {})
    print(f"\n==================  {nombre}  ({RIC})  ==================")
    print((_periodo(df) if period == "TR.Revenue.date" else
           df.drop(columns=["Instrument"], errors="ignore")).to_string(index=False))
    if err:
        print(f"--- avisos {nombre} (campos que no resolvieron):")
        for e in err:
            print("   ", e)
    return df


# ── Estados ────────────────────────────────────────────────────────────────
inc = traer("INCOME STATEMENT", INCOME, Q)
traer("BALANCE SHEET", BALANCE, Q)
traer("CASH FLOW", CASHFLOW, Q)

# ── Márgenes (calculados desde el income) ──────────────────────────────────
try:
    m = _periodo(inc.copy())
    rev = m["Revenue"]
    mar = pd.DataFrame({"Periodo": m["Periodo"]})
    mar["Margen Bruto %"] = (m["Gross Profit"] / rev * 100).round(1)
    mar["Margen EBITDA %"] = (m["EBITDA"] / rev * 100).round(1)
    mar["Margen Operativo %"] = (m["Operating Income"] / rev * 100).round(1)
    mar["Margen Neto %"] = (m["Net Income After Taxes"] / rev * 100).round(1)
    print("\n==================  MÁRGENES (calculados)  ==================")
    print(mar.to_string(index=False))
except Exception as e:
    print(f"\n(no pude calcular márgenes: {e})")

# ── Ratios / valuación / snapshot / estimados / segmentos ──────────────────
traer("RATIOS / VALUACIÓN", RATIOS, Q)
traer("SNAPSHOT DE MERCADO", SNAPSHOT, None, period=None)
traer("ESTIMADOS (CONSENSO)", ESTIMATES, FY, period="TR.RevenueMeanEstimate.fperiod")

# Segmentos: estructura distinta (una fila por segmento). Tentativo.
seg, serr = ek.get_data(
    [RIC],
    ["TR.BGS.BusinessTotalRevenue.segmentName", "TR.BGS.BusinessTotalRevenue"],  # ?
    {"Period": "FQ0", "Frq": "FQ", "SDate": "0", "EDate": "-3"},
)
print(f"\n==================  SEGMENTOS (ingresos)  ({RIC})  ==================")
print(seg.drop(columns=["Instrument"], errors="ignore").to_string(index=False))
if serr:
    print("--- avisos segmentos:")
    for e in serr:
        print("   ", e)

print("\nValores en unidades (÷1.000.000 = millones). Pegame la salida: corrijo los "
      "# ? que fallen y ajustamos qué dejamos. Después lo modelamos y conectamos a la base.")
