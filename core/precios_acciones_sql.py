"""core/precios_acciones_sql.py — velas EOD de `mercado.precios_acciones`.

Lectura de infra, sin cálculo: quien necesita velas las pide acá y se las pasa
a `quant/` (que no toca la base: `test_capas.test_quant_es_calculo_puro`).
Escribe la tabla `jobs/precios_acciones_daily.py` (Yahoo, post-cierre US).

Shape de cada vela: `{"fecha": datetime naive 00h, "high", "low", "close"}`
con floats o None. `fecha` va como datetime (no date) porque así lo consumen
los pivots y el front desde antes del cutover a SQL.
"""
from __future__ import annotations

from datetime import date, datetime

from core.postgres import get_pool


def _dt(d: date) -> datetime:
    return datetime(d.year, d.month, d.day)


def _f(v) -> float | None:
    return float(v) if v is not None else None


def velas_eod(ticker: str, desde: date, hasta: date) -> list[dict]:
    """Velas en [desde, hasta), ascendentes por fecha."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT fecha, high, low, close FROM mercado.precios_acciones "
            "WHERE ticker = %s AND fecha >= %s AND fecha < %s ORDER BY fecha",
            (ticker, desde, hasta),
        )
        rows = cur.fetchall()
    return [{"fecha": _dt(r[0]), "high": _f(r[1]), "low": _f(r[2]), "close": _f(r[3])}
            for r in rows]


def ultima_vela(ticker: str) -> dict | None:
    """La vela más reciente del ticker (solo fecha y close), o None si no hay."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT fecha, close FROM mercado.precios_acciones "
            "WHERE ticker = %s ORDER BY fecha DESC LIMIT 1",
            (ticker,),
        )
        r = cur.fetchone()
    if not r:
        return None
    return {"fecha": _dt(r[0]), "close": _f(r[1])}
