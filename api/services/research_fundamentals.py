"""Capa de servicio — Análisis Fundamental (módulo Renta Variable).

Lee research.{companies, fundamentals, market_snapshot} (fundamentals de
Refinitiv/LSEG, ingestados por scripts/refinitiv_fundamentals.py) y arma los datos
de los 4 paneles de la vista Análisis Fundamental. Read-only, SQL-only.

Paneles: (1) ficha + mercado, (2) evolución (ingresos/EBITDA/neto),
(3) márgenes calculados + ratios, (4) ingresos por segmento.
"""
from __future__ import annotations

from psycopg.rows import dict_row

from api.cache import cached
from core.postgres import get_pool


@cached(ttl=300)
def list_companies() -> list[dict]:
    """Universo para el selector de empresa. Ordenado por nombre."""
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


def _f(v) -> float | None:
    return float(v) if v is not None else None


def _pick(d: dict, sub: str):
    """Primer valor cuyo item contiene `sub` (case-insensitive)."""
    for k, v in d.items():
        if sub.lower() in k.lower():
            return v
    return None


@cached(ttl=300)
def get_analisis(ric: str, freq: str = "FY") -> dict:
    """Datos de los 4 paneles para un RIC. `freq` = 'FY' (anual) | 'Q' (trimestral)."""
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

    # income + ratios pivotados por período (fiscal_period), ordenados por period_end
    orden: dict[str, object] = {}
    income: dict[str, dict] = {}
    ratios: dict[str, dict] = {}
    for r in rows:
        fp = r["fiscal_period"]
        orden.setdefault(fp, r["period_end"])
        if r["statement"] == "income":
            income.setdefault(fp, {})[r["item"]] = _f(r["value"])
        elif r["statement"] == "ratios":
            ratios.setdefault(fp, {})[r["item"]] = _f(r["value"])
    periodos = sorted(income, key=lambda p: orden[p])

    evolucion, margenes, ratios_arr = [], [], []
    for fp in periodos:
        inc = income.get(fp, {})
        rev = _pick(inc, "Revenue")
        ebitda = _pick(inc, "EBITDA")
        neto = _pick(inc, "Net Income")
        gross = _pick(inc, "Gross Profit")
        oper = _pick(inc, "Operating Income")
        evolucion.append({"periodo": fp, "ingresos": rev, "ebitda": ebitda, "neto": neto})

        def _m(x, r=rev):
            return round(x / r * 100, 1) if r and x is not None else None

        margenes.append({"periodo": fp, "bruto": _m(gross), "ebitda": _m(ebitda),
                         "operativo": _m(oper), "neto": _m(neto)})
        rr = ratios.get(fp, {})
        ratios_arr.append({"periodo": fp, "roe": _pick(rr, "Return On Equity"),
                           "roa": _pick(rr, "Return On Assets"),
                           "ps": _pick(rr, "Price To Sales"),
                           "pb": _pick(rr, "Price To Book")})

    # segmentos: última fecha disponible (se ingestan siempre en trimestral)
    seg_rows = [r for r in _rows(ric, "Q") if r["statement"] == "segment" and r["segment"]]
    segmentos = []
    if seg_rows:
        ultimo = max(r["period_end"] for r in seg_rows)
        segmentos = [{"segmento": r["segment"], "valor": _f(r["value"])}
                     for r in seg_rows if r["period_end"] == ultimo]

    return {
        "company": company,
        "market": market,
        "periodos": periodos,
        "evolucion": evolucion,
        "margenes": margenes,
        "ratios": ratios_arr,
        "segmentos": segmentos,
    }
