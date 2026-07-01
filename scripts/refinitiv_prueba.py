"""refinitiv_prueba.py — PRUEBA de conexión al API de Eikon (RKLB.O).

Standalone: NO depende del repo. Copialo a tu PC (con Workspace/Eikon ABIERTO),
pegá tu app key abajo, y corrélo:  python refinitiv_prueba.py

De momento SOLO imprime el response (no escribe a la base). La idea es confirmar
que conecta y ver qué campos vuelven, para después mapearlos al Excel de referencia
(Income Statement / Balance / Cash Flow / Segmentos) y recién ahí guardarlos en SQL.

Requisitos en tu PC:
    pip install eikon
    (y tener Refinitiv Workspace/Eikon abierto y logueado)
"""
import eikon as ek

# ── 1) Configurar API Key ──────────────────────────────────────────────────
ek.set_app_key("PEGA_TU_APP_KEY_ACA")

# ── 2) Qué pedimos ─────────────────────────────────────────────────────────
RIC = "RKLB.O"

# Campos del INCOME STATEMENT (arrancamos por uno solo para probar).
# El comentario es la línea equivalente del Excel de referencia. Si algún campo
# vuelve vacío o con error, ajustamos el nombre con el Data Item Browser (el "?"
# al lado del dato en Workspace) — get_data no rompe, avisa cuál falló.
CAMPOS = [
    "TR.Revenue.date",            # fecha de fin de período
    "TR.Revenue",                 # Revenue from Business Activities - Total
    "TR.CostOfRevenueTotal",      # Cost of Revenues - Total
    "TR.GrossProfit",             # Gross Profit - Total
    "TR.OperatingIncome",         # Operating Profit
    "TR.EBITDA",                  # EBITDA
    "TR.NetIncomeAfterTaxes",     # Resultado neto
    "TR.EPSActValue",             # BPA (EPS)
]

# Últimos ~8 trimestres (como el Excel, que trae varios períodos).
PARAMS = {"Period": "FQ0", "Frq": "FQ", "SDate": "0", "EDate": "-7"}

# ── 3) Traer e imprimir ────────────────────────────────────────────────────
df, err = ek.get_data([RIC], CAMPOS, PARAMS)

print("\n=== RESPONSE ===")
print(df.to_string())

if err:
    print("\n=== AVISOS/ERRORES POR CAMPO (si alguno no resolvió) ===")
    print(err)

print("\nListo. Pegame esta salida y con eso mapeo los campos al Excel y sumo "
      "Balance / Cash Flow / Segmentos, y después lo conectamos a la base.")
