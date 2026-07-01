"""refinitiv_prueba.py — PRUEBA del API de Eikon (RKLB.O), ampliada.

Standalone: NO depende del repo. Copialo a tu PC (con Workspace/Eikon ABIERTO),
pegá tu app key abajo, y corrélo:  python refinitiv_prueba.py

De momento SOLO imprime el response (no escribe a la base). Ahora trae los TRES
estados (Income Statement / Balance Sheet / Cash Flow), últimos ~8 trimestres, y
agrega una columna `Periodo` (Año + Trimestre) para leerlo como el Excel.

Los nombres de campo marcados con  # ?  son tentativos: si alguno no resuelve,
get_data NO rompe — lo lista en la sección de avisos. Corrélo, pegame la salida y
corrijo los que fallen + sigo sumando (segmentos, ratios, etc.).

Requisitos en tu PC:
    pip install eikon pandas
    (y tener Refinitiv Workspace/Eikon abierto y logueado)
"""
import eikon as ek
import pandas as pd

pd.set_option("display.width", 240)
pd.set_option("display.max_columns", 60)

# ── 1) Configurar API Key ──────────────────────────────────────────────────
ek.set_app_key("PEGA_TU_APP_KEY_ACA")

# ── 2) Qué pedimos ─────────────────────────────────────────────────────────
RIC = "RKLB.O"
PARAMS = {"Period": "FQ0", "Frq": "FQ", "SDate": "0", "EDate": "-7"}  # últimos 8 trimestres

# Cada lista mapea a las líneas del Excel de referencia. Los  # ?  son a confirmar.
INCOME = [
    "TR.Revenue",                 # Revenue from Business Activities - Total
    "TR.CostOfRevenueTotal",      # Cost of Revenues - Total
    "TR.GrossProfit",             # Gross Profit - Total
    "TR.SGATotal",                # ? Selling, General & Admin - Total
    "TR.ResearchAndDevelopment",  # ? Research & Development Expense
    "TR.TotalOperatingExpense",   # ? Operating Expenses - Total
    "TR.OperatingIncome",         # Operating Profit
    "TR.EBITDA",                  # EBITDA
    "TR.DepreciationAmort",       # ? Depreciación & amortización
    "TR.PretaxIncome",            # ? Resultado antes de impuestos
    "TR.IncomeTaxes",             # ? Impuesto a las ganancias
    "TR.NetIncomeAfterTaxes",     # Resultado neto
    "TR.EPSActValue",             # BPA (EPS)
]

BALANCE = [
    "TR.CashAndSTInvestments",    # ? Cash & Short-Term Investments
    "TR.CashAndEquivalents",      # ? Cash & Cash Equivalents
    "TR.TotalReceivablesNet",     # ? Loans & Receivables - Net
    "TR.TotalInventory",          # ? Inventories - Total
    "TR.TotalCurrentAssets",      # Total Current Assets
    "TR.NetPPE",                  # ? Property, Plant & Equipment - Net
    "TR.TotalAssets",             # ? Total Assets
    "TR.TotalCurrentLiabilities", # ? Total Current Liabilities
    "TR.TotalDebt",               # ? Total Debt
    "TR.TotalLiabilities",        # ? Total Liabilities
    "TR.TotalEquity",             # ? Total Shareholders' Equity
]

CASHFLOW = [
    "TR.CashFromOperatingActivities",  # ? Operating Cash Flow
    "TR.CapitalExpenditures",          # ? CapEx
    "TR.FreeCashFlow",                 # ? Free Cash Flow
    "TR.CashFromInvestingActivities",  # ? Cash from Investing
    "TR.CashFromFinancingActivities",  # ? Cash from Financing
    "TR.NetChangeInCash",              # ? Variación neta de caja
]


def _periodo(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega columna 'Periodo' (Año + Trimestre) a partir de la fecha de cierre.
    Nota: el trimestre se deriva del mes (cierre Dic = Q4). Vale para calendarios
    fiscales que cierran en diciembre (RKLB). Para otros habría que usar el período
    fiscal de Refinitiv."""
    if "Date" not in df.columns:
        return df
    d = pd.to_datetime(df["Date"])
    df.insert(0, "Periodo", d.dt.year.astype(str) + " Q" + (((d.dt.month - 1) // 3) + 1).astype(str))
    return df.drop(columns=["Date", "Instrument"], errors="ignore")


def traer(nombre: str, campos: list[str]) -> None:
    # "TR.Revenue.date" adelante → nos da la columna Date del período.
    df, err = ek.get_data([RIC], ["TR.Revenue.date", *campos], PARAMS)
    print(f"\n===================  {nombre}  ({RIC})  ===================")
    print(_periodo(df).to_string(index=False))
    if err:
        print(f"--- avisos {nombre} (campos que no resolvieron):")
        for e in err:
            print("   ", e)


# ── 3) Traer e imprimir ────────────────────────────────────────────────────
traer("INCOME STATEMENT", INCOME)
traer("BALANCE SHEET", BALANCE)
traer("CASH FLOW", CASHFLOW)

print("\nValores en UNIDADES (÷ 1.000.000 = los millones del Excel); el BPA va en "
      "unidades por acción.\nPegame la salida: corrijo los campos con # ? que fallen "
      "y sumo segmentos + ratios. Después lo conectamos a la base.")
