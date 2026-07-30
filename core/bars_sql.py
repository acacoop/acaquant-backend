"""bars_sql.py — readers de las barras de 1 minuto de CEDEARs (ARS).

Dos fuentes para la MISMA serie de cierres por minuto:
  - HOY / en vivo: `mercado.cedears_time_sales` (el tape, se vacía al cierre) —
    resampleado al vuelo, para el Efficiency Ratio live durante la rueda.
  - HISTÓRICO: `mercado.cedears_bars_1m` (archivo permanente que escribe
    jobs/cedears_bars_1m.py antes del cleanup).

El ER se deriva de estas series con quant.rango.efficiency_ratio_ventanas — el
número no se persiste; la fuente de verdad son las barras.
"""
from __future__ import annotations

from datetime import date

from core.postgres import get_pool


def closes_1m_hoy(ticker_corto: str) -> list[float]:
    """Cierres por minuto del tape VIVO de hoy (asc). El close del minuto = el
    último precio del minuto. Vacío tras el cleanup del tape."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT (array_agg(price ORDER BY ts DESC, id DESC))[1] AS c
               FROM mercado.cedears_time_sales
               WHERE ticker_corto = %s AND price > 0
               GROUP BY date_trunc('minute', ts)
               ORDER BY date_trunc('minute', ts)""",
            (ticker_corto.upper(),),
        )
        return [float(r[0]) for r in cur.fetchall() if r[0] is not None]


def closes_1m(ticker_corto: str, fecha: date) -> list[float]:
    """Cierres por minuto de una rueda del ARCHIVO (asc)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT close FROM mercado.cedears_bars_1m
               WHERE ticker_corto = %s AND minuto::date = %s AND close IS NOT NULL
               ORDER BY minuto ASC""",
            (ticker_corto.upper(), fecha),
        )
        return [float(r[0]) for r in cur.fetchall()]


def efficiency_ratio_live(ticker_corto: str, ventana_reciente: int = 30) -> dict:
    """ER intradía de HOY (vivo, desde el tape): {er_dia, er_reciente}. Los
    valores pueden ser None si no hay barras suficientes."""
    from quant.rango import efficiency_ratio_ventanas
    return efficiency_ratio_ventanas(closes_1m_hoy(ticker_corto), ventana_reciente)


def efficiency_ratio_hist(ticker_corto: str, fecha: date, ventana_reciente: int = 30) -> dict:
    """ER intradía de una rueda pasada (desde el archivo): {er_dia, er_reciente}."""
    from quant.rango import efficiency_ratio_ventanas
    return efficiency_ratio_ventanas(closes_1m(ticker_corto, fecha), ventana_reciente)
