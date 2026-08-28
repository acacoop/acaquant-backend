"""api/services/rv_motor.py — matriz de correlación del universo USD (Renta Variable).

Lo que queda de la Mesa de Estrategia: `get_correlation_matrix`, que alimenta
`GET /api/scanner/companeros/{ticker}` vía `api/services/day_trading.py`.

`get_trade_analysis` y `get_book_analysis` se BORRARON el 2026-08-28 con el MCP:
sus endpoints HTTP ya se habían eliminado el 2026-07-13 (sin tabs que los usaran)
y desde entonces su único caller eran las tools del MCP. Medido antes de borrar:
cero consumidores. `get_correlation_matrix` NO estaba en ese grupo — por eso se
queda.

Trabaja sobre el precio del subyacente USD (`Trading.PreciosAcciones`), no
sobre el CEDEAR en ARS. Las series se alinean por FECHA — la correlación se
calcula sobre los mismos días para todos los tickers — y la ventana es común
a todo el set, así la matriz sirve para optimización.
"""
from __future__ import annotations

from api.cache import cached
from quant.rolling_stats import correlation, realized_vol, returns_from_prices

# Mínimo de observaciones para incluir un ticker / dar una correlación creíble.
_MIN_OBS = 30


def _universo_map() -> dict[str, str]:
    """{ticker_corto: underlying} de todo el master mercado.cedears (SQL-native,
    decomiso Mongo: Trading.Cedears dropeada)."""
    from core.postgres import get_pool
    out: dict[str, str] = {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker_corto, underlying FROM mercado.cedears")
        for tc, und in cur.fetchall():
            if tc:
                out[tc.upper()] = (und or tc).upper()
    return out


@cached(ttl=300)
def get_correlation_matrix(
    tickers: tuple[str, ...] | None = None,
    ventana_dias: int = 252,
) -> dict:
    """Matriz de correlación de retornos diarios.

    Args:
        tickers: tuple de ticker_corto. None = todo el universo de
            `Trading.Cedears`.
        ventana_dias: ventana de días hábiles comunes (default 252 ≈ 1 año).

    Returns:
        {
          tickers:      [...],           # los efectivamente incluidos
          excluidos:    [...],           # sin serie suficiente (< 30 obs)
          n_obs:        int,             # retornos comunes usados
          fecha_desde / fecha_hasta: "YYYY-MM-DD" | None,
          ventana_dias: int,
          matriz:       [[float|None]],  # correlación; orden = `tickers`
          vol_anual:    {ticker: float|None},  # vol realizada anualizada
        }
        La covarianza se reconstruye: `cov_ij = matriz_ij × vol_i × vol_j`.
    """
    universo = _universo_map()

    if tickers:
        pedidos = {t.upper(): universo.get(t.upper(), t.upper()) for t in tickers}
    else:
        pedidos = universo

    # Precios de todos los underlyings en UNA query a mercado.precios_acciones (SQL).
    # Cutover PreciosAcciones→SQL (2026-06-24): antes leía Trading.PreciosAcciones (Mongo).
    from core.postgres import get_pool
    underlyings = sorted(set(pedidos.values()))
    por_underlying: dict[str, dict[str, float]] = {}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, fecha, close FROM mercado.precios_acciones "
            "WHERE ticker = ANY(%s) AND close IS NOT NULL ORDER BY fecha",
            (underlyings,))
        for ticker, fecha, close in cur.fetchall():
            por_underlying.setdefault(ticker, {})[fecha.isoformat()] = float(close)

    series: dict[str, dict[str, float]] = {}
    excluidos: list[str] = []
    for tc, und in pedidos.items():
        s = por_underlying.get(und, {})
        if len(s) >= _MIN_OBS:
            series[tc] = s
        else:
            excluidos.append(tc)

    incluidos = sorted(series.keys())
    base = {
        "tickers": incluidos,
        "excluidos": sorted(excluidos),
        "ventana_dias": ventana_dias,
    }
    if len(incluidos) < 2:
        return {**base, "n_obs": 0, "fecha_desde": None, "fecha_hasta": None,
                "matriz": [], "vol_anual": {}}

    # Fechas comunes a TODOS los incluidos → ventana consistente.
    # +1 porque returns_from_prices consume una observación.
    fechas = sorted(set.intersection(*[set(series[t]) for t in incluidos]))
    fechas = fechas[-(ventana_dias + 1):]
    if len(fechas) < _MIN_OBS:
        return {**base, "n_obs": max(len(fechas) - 1, 0),
                "fecha_desde": fechas[0] if fechas else None,
                "fecha_hasta": fechas[-1] if fechas else None,
                "matriz": [], "vol_anual": {}}

    rets = {
        t: returns_from_prices([series[t][f] for f in fechas])
        for t in incluidos
    }

    # Matriz simétrica — se computa solo el triángulo superior.
    n = len(incluidos)
    matriz: list[list[float | None]] = [[None] * n for _ in range(n)]
    for i in range(n):
        matriz[i][i] = 1.0
        for j in range(i + 1, n):
            c = correlation(rets[incluidos[i]], rets[incluidos[j]])
            matriz[i][j] = c
            matriz[j][i] = c

    return {
        **base,
        "n_obs": len(fechas) - 1,
        "fecha_desde": fechas[0],
        "fecha_hasta": fechas[-1],
        "matriz": matriz,
        "vol_anual": {t: realized_vol(rets[t]) for t in incluidos},
    }


# ── Módulo 1 — análisis de trade individual + hedge-finder ──────────────────


# ── Módulo 2 — análisis de book / exposición ────────────────────────────────
