"""Lectura de mercado.market_snapshot (SQL) — estado live por ticker. SQL-only.

Espejo de Trading.MarketSnapshot (en baja). Columnar: una columna por métrica
(last_price, tea, tem, paridad, duration, mod_duration, convexity, vwap,
total_nominals, open/high/low/closing_price, book jsonb). Lo escriben los motores
valores.py (book+precios) y curvas.py (analíticos) vía pg_mirror.

Helpers para los motores que antes leían Trading.MarketSnapshot.metrics directo.
"""
from __future__ import annotations

from decimal import Decimal as _Decimal

from core.postgres import get_pool

# Columna SQL → clave del sub-doc `metrics` de Mongo (mayúsculas TEA/TEM como el motor).
_COL_TO_KEY = {
    "total_nominals": "total_nominals", "vwap": "vwap", "last_price": "last_price",
    "open_price": "open_price", "high_price": "high_price", "low_price": "low_price",
    "closing_price": "closing_price", "tea": "TEA", "tem": "TEM", "duration": "duration",
    "mod_duration": "mod_duration", "convexity": "convexity", "paridad": "paridad",
}


def snapshot_docs(tickers) -> dict[str, dict]:
    """{ticker: {"ticker", "metrics": {clave Mongo: val} (sin None), "book": jsonb|None,
    "updated_at"}} — equivalente al doc de Trading.MarketSnapshot. Drop-in para los
    lectores que hacían db.MarketSnapshot.find y accedían doc['metrics']/['book']."""
    tickers = list(tickers)
    if not tickers:
        return {}
    cols = ", ".join(_COL_TO_KEY)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT ticker, book, updated_at, {cols} FROM mercado.market_snapshot "
            "WHERE ticker = ANY(%s)",
            (tickers,),
        )
        names = [d.name for d in cur.description]
        rows = cur.fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        d = dict(zip(names, r))
        metrics = {key: float(d[col]) for col, key in _COL_TO_KEY.items() if d.get(col) is not None}
        out[d["ticker"]] = {
            "ticker": d["ticker"], "metrics": metrics,
            "book": d.get("book"), "updated_at": d.get("updated_at"),
        }
    return out


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


def last_prices(tickers) -> list[dict]:
    """[{ticker, last_price, updated_at}] con last_price > 0. Lean (sin book jsonb)
    para el loop de curvas, que lo lee cada pocos segundos."""
    tickers = list(tickers)
    if not tickers:
        return []
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, last_price, updated_at FROM mercado.market_snapshot "
            "WHERE ticker = ANY(%s) AND last_price > 0",
            (tickers,),
        )
        return [{"ticker": r[0], "last_price": float(r[1]), "updated_at": r[2]}
                for r in cur.fetchall()]


def _num(v):
    """`numeric` de Postgres → float; **cualquier otra cosa se devuelve tal cual**.

    El cast era incondicional y reventaba con la primera columna no numérica
    (`updated_at`): `float() argument must be a string or a real number, not
    'datetime.datetime'`. La función se llama `cols_map`, no `metricas_map` —
    prometía servir cualquier columna del snapshot y solo servía las numéricas,
    en una tabla que tiene `book jsonb` y dos timestamps.

    Convertir solo lo que ES un número no cambia nada para los 7 callers que hoy
    piden `tea`/`duration`/`last_price`: `Decimal` sigue saliendo `float`.
    """
    return float(v) if isinstance(v, (int, float, _Decimal)) else v


def cols_map(tickers, cols: list[str]) -> dict[str, dict]:
    """{ticker: {col: valor|None}} para varias columnas. Sin filtro de NULL —
    el caller decide. Para casos que necesitan varias métricas juntas (ej. TEA+duration).

    Las columnas numéricas vuelven como `float`; las que no lo son (timestamps,
    jsonb) vuelven con su tipo — ver `_num`."""
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
            out[r[0]] = {c: (_num(v) if v is not None else None)
                         for c, v in zip(cols, r[1:], strict=True)}
        return out
