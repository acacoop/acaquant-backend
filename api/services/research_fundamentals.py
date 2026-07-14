"""Capa de servicio — Análisis Fundamental (módulo Renta Variable).

Lee research.{companies, fundamentals, market_snapshot} (fundamentals de
Refinitiv/LSEG, ingestados por scripts/refinitiv_fundamentals.py) y arma la vista
Análisis Fundamental estilo informe (tablas de estados + múltiplos/ratios +
márgenes + segmentos). Read-only, SQL-only.
"""
from __future__ import annotations

from psycopg.rows import dict_row

from api.cache import cached
from api.services._sql import _f
from core.postgres import get_pool

# Orden de presentación por estado (prefijo del label de Refinitiv → índice de fila).
# Los estados no traen ordinal en la base; ordenamos por este prefijo (startswith).
_ORDER: dict[str, tuple[str, ...]] = {
    "income": (
        "Revenue", "Cost of Revenue", "Gross Profit", "Research", "Total Operating",
        "Operating Income", "EBITDA", "Depreciation", "Pretax", "Income Tax",
        "Net Income", "Earnings Per Share",
    ),
    "balance": (
        "Cash and Short", "Total Receivable", "Total Inventory", "Total Current Assets",
        "Total Assets", "Total Current Liabilit", "Total Debt", "Total Liabilit", "Total Equity",
    ),
    "cashflow": (
        "Cash from Operating", "Capital Expenditure", "Free Cash",
        "Cash from Investing", "Cash from Financing",
    ),
    "ratios": (
        "P/E", "Enterprise Value", "Price To Sales", "Price To Book",
        "Return On Equity", "Return On Assets",
    ),
}

# Etiqueta de presentación (label largo de Refinitiv → nombre corto/es). El orden
# se calcula con el label ORIGINAL (_ORDER); esto es solo lo que se muestra.
_LABEL: dict[str, str] = {
    "Revenue": "Ingresos",
    "Cost of Revenue, Total": "Costo de ventas",
    "Gross Profit": "Ganancia bruta",
    "Research And Development": "I + D",
    "Total Operating Expense": "Gastos operativos",
    "Operating Income": "Resultado operativo",
    "Depreciation And Amortization": "Amortizaciones",
    "Net Income After Taxes": "Resultado neto",
    "Earnings Per Share - Actual": "BPA",
    "Cash and Short Term Investments": "Caja e inv. CP",
    "Total Receivables, Net": "Créditos por ventas",
    "Total Inventory": "Inventarios",
    "Total Current Assets": "Activo corriente",
    "Total Assets": "Activo total",
    "Total Current Liabilities": "Pasivo corriente",
    "Total Debt": "Deuda total",
    "Total Liabilities": "Pasivo total",
    "Total Equity": "Patrimonio neto",
    "Cash from Operating Activities": "Flujo operativo",
    "Capital Expenditures, Cumulative": "CapEx (acum.)",
    "Free Cash Flow": "Flujo de caja libre",
    "Cash from Investing Activities": "Flujo de inversión",
    "Cash from Financing Activities": "Flujo de financiación",
    "P/E (Daily Time Series Ratio)": "P / E",
    "Enterprise Value To EBITDA (Daily Time Series Ratio)": "EV / EBITDA",
    "Price To Sales Per Share (Daily Time Series Ratio)": "P / Ventas",
    "Price To Book Value Per Share (Daily Time Series Ratio)": "P / VL",
    "Return On Equity - Actual": "ROE",
    "Return On Assets - Actual": "ROA",
}


@cached(ttl=300)
def list_companies() -> list[dict]:
    """Universo para el selector de empresa."""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT ric, ticker, nombre, sector FROM research.companies "
            "WHERE activo IS NOT false ORDER BY nombre NULLS LAST, ric"
        )
        return cur.fetchall()


def _rows(ric: str, freq: str) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT statement, period_end, fiscal_period, item, segment, value "
            "FROM research.fundamentals WHERE ric = %s AND freq = %s ORDER BY period_end",
            (ric, freq),
        )
        return cur.fetchall()


def _orden_item(statement: str, item: str) -> tuple[int, str]:
    order = _ORDER.get(statement, ())
    for i, sub in enumerate(order):
        if item.lower().startswith(sub.lower()):
            return (i, item)
    return (len(order), item)  # desconocidos al final, alfabético


@cached(ttl=300)
def get_analisis(ric: str, freq: str = "FY") -> dict:
    """Datos de la vista para un RIC. `freq` = 'FY' (anual) | 'Q' (trimestral).

    Devuelve tablas pivoteadas (item × período) por estado, márgenes calculados y
    los ingresos por segmento (tabla).
    """
    freq = "Q" if str(freq).upper().startswith("Q") else "FY"
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT ric, ticker, nombre, sector, pais, bolsa, moneda, cedear_ticker "
            "FROM research.companies WHERE ric = %s", (ric,),
        )
        company = cur.fetchone()
        cur.execute(
            "SELECT ric, price, high_52w, low_52w, market_cap, ev, shares, div_yield, "
            "currency, updated_at FROM research.market_snapshot WHERE ric = %s", (ric,),
        )
        market = cur.fetchone()

    rows = _rows(ric, freq)

    # pivot: statement -> item -> {fiscal_period: value}; y orden de períodos por period_end
    by_stmt: dict[str, dict[str, dict]] = {}
    orden_p: dict[str, object] = {}
    for r in rows:
        if r["statement"] == "segment":
            continue
        orden_p.setdefault(r["fiscal_period"], r["period_end"])
        by_stmt.setdefault(r["statement"], {}).setdefault(r["item"], {})[r["fiscal_period"]] = _f(r["value"])
    # últimos N períodos (para que la tabla no se desborde): 6 años / 8 trimestres
    periodos = sorted(orden_p, key=lambda p: orden_p[p])[-(8 if freq == "Q" else 6):]

    def tabla(stmt: str) -> list[dict]:
        items = by_stmt.get(stmt, {})
        ordenados = sorted(items, key=lambda it: _orden_item(stmt, it))
        return [{"item": _LABEL.get(it, it), "valores": [items[it].get(fp) for fp in periodos]}
                for it in ordenados]

    tablas = {s: tabla(s) for s in ("income", "balance", "cashflow", "ratios")}

    # márgenes calculados desde el income
    inc = by_stmt.get("income", {})

    def _rowval(sub: str, fp: str):
        for it, vals in inc.items():
            if sub.lower() in it.lower():
                return vals.get(fp)
        return None

    margenes = []
    for fp in periodos:
        rev = _rowval("Revenue", fp)

        def _mg(x, r=rev):
            return round(x / r * 100, 1) if r and x is not None else None

        margenes.append({
            "periodo": fp,
            "bruto": _mg(_rowval("Gross Profit", fp)),
            "ebitda": _mg(_rowval("EBITDA", fp)),
            "operativo": _mg(_rowval("Operating Income", fp)),
            "neto": _mg(_rowval("Net Income", fp)),
        })

    # segmentos: tabla segmento × período (siempre trimestral), últimos 6.
    # Se filtran los pseudo-segmentos agregados ('... Total', 'Consolidated').
    seg_rows = [r for r in _rows(ric, "Q")
                if r["statement"] == "segment" and r["segment"]
                and "total" not in r["segment"].lower()
                and "consolidated" not in r["segment"].lower()]
    seg_by: dict[str, dict] = {}
    seg_orden: dict[str, object] = {}
    for r in seg_rows:
        seg_orden.setdefault(r["fiscal_period"], r["period_end"])
        seg_by.setdefault(r["segment"], {})[r["fiscal_period"]] = _f(r["value"])
    seg_periodos = sorted(seg_orden, key=lambda p: seg_orden[p])[-6:]
    segmentos = {
        "periodos": seg_periodos,
        "filas": [{"segmento": s, "valores": [seg_by[s].get(fp) for fp in seg_periodos]}
                  for s in sorted(seg_by)],
    }

    return {
        "company": company,
        "market": market,
        "periodos": periodos,
        "tablas": tablas,
        "margenes": margenes,
        "segmentos": segmentos,
    }
