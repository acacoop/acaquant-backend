"""api/services/rv_motor.py — motor de la Mesa de Estrategia (Renta Variable).

Primera pieza del feature de `docs/wip_mesa_estrategia_rv.md` ("Paso 0 — el
motor"): la matriz de correlación del universo de activos USD. Es la base del
hedge-finder (correlación de un activo vs el resto) y de la optimización de
carteras.

Trabaja sobre el precio del subyacente USD (`Trading.PreciosAcciones`), no
sobre el CEDEAR en ARS. Las series se alinean por FECHA — la correlación se
calcula sobre los mismos días para todos los tickers — y la ventana es común
a todo el set, así la matriz sirve para optimización.
"""
from __future__ import annotations

from api.cache import cached
from api.db import get_db_trading
from quant.rolling_stats import correlation, realized_vol, returns_from_prices

# Mínimo de observaciones para incluir un ticker / dar una correlación creíble.
_MIN_OBS = 30


def _universo_map() -> dict[str, str]:
    """{ticker_corto: underlying} de todo el master Trading.Cedears."""
    db = get_db_trading()
    out: dict[str, str] = {}
    for d in db["Cedears"].find({}, {"_id": 0, "ticker_corto": 1, "underlying": 1}):
        tc = d.get("ticker_corto")
        if tc:
            out[tc.upper()] = (d.get("underlying") or tc).upper()
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
    db = get_db_trading()
    universo = _universo_map()

    if tickers:
        pedidos = {t.upper(): universo.get(t.upper(), t.upper()) for t in tickers}
    else:
        pedidos = universo

    # Precios de todos los underlyings en UNA query.
    docs = db["PreciosAcciones"].find(
        {"ticker": {"$in": sorted(set(pedidos.values()))}},
        projection={"_id": 0, "ticker": 1, "fecha": 1, "close": 1},
        sort=[("fecha", 1)],
    )
    por_underlying: dict[str, dict[str, float]] = {}
    for d in docs:
        if d.get("close") is None or not d.get("fecha"):
            continue
        por_underlying.setdefault(d["ticker"], {})[str(d["fecha"])[:10]] = d["close"]

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
