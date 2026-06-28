"""Lectura de mercado.market_snapshot (SQL) — estado live por ticker. SQL-only.

Espejo de Trading.MarketSnapshot (en baja). Columnar: una columna por métrica
(last_price, tea, tem, paridad, duration, mod_duration, convexity, vwap,
total_nominals, open/high/low/closing_price, book jsonb). Lo escriben los motores
valores.py (book+precios) y curvas.py (analíticos) vía pg_mirror.

Helpers para los motores que antes leían Trading.MarketSnapshot.metrics directo.
"""
from __future__ import annotations

from core.postgres import get_pool


def metric_map(tickers, col: str, positivo: bool = False) -> dict[str, float]:
    """{ticker: valor} de UNA métrica para los tickers dados. Excluye NULL; si
    positivo=True exige > 0. `col` es la columna SQL (last_price, tem, paridad…)."""
    tickers = list(tickers)
    if not tickers:
        return {}
    cond = f" AND {col} > 0" if positivo else f" AND {col} IS NOT NULL"
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT ticker, {col} FROM mercado.market_snapshot "
            f"WHERE ticker = ANY(%s){cond}",
            (tickers,),
        )
        return {r[0]: float(r[1]) for r in cur.fetchall()}


def cols_map(tickers, cols: list[str]) -> dict[str, dict]:
    """{ticker: {col: valor|None}} para varias columnas. Sin filtro de NULL —
    el caller decide. Para casos que necesitan varias métricas juntas (ej. TEA+duration)."""
    tickers = list(tickers)
    if not tickers:
        return {}
    sel = ", ".join(["ticker", *cols])
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {sel} FROM mercado.market_snapshot WHERE ticker = ANY(%s)",
            (tickers,),
        )
        out: dict[str, dict] = {}
        for r in cur.fetchall():
            out[r[0]] = {c: (float(v) if v is not None else None) for c, v in zip(cols, r[1:])}
        return out
