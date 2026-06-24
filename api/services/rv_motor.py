"""api/services/rv_motor.py — motor de la Mesa de Estrategia (Renta Variable).

Primera pieza de la Mesa de Estrategia ("Paso 0 — el
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

_SQRT252 = 252 ** 0.5
_SQRT12 = 12 ** 0.5
_Z95 = 1.645   # cuantil 95% normal — para el VaR 1 día


def get_trade_analysis(ticker: str, monto: float, direccion: str = "long") -> dict:
    """Analiza un trade individual: caracterización de riesgo + hedge-finder.

    Módulo 1 de la Mesa de Estrategia (UI: acaquant-web /retorno → TRADE LAB).

    Args:
        ticker: ticker_corto del activo.
        monto: tamaño del trade en USD.
        direccion: "long" | "short".

    Returns:
        {
          trade:            {ticker, monto, direccion, last},
          caracterizacion:  vol, beta, zscore, VaR 1d, peor mes,
                            exposición de mercado equivalente,
          hedge_beta:       hedge directo vs SPY/QQQ (notional + acción),
          hedge_finder:     universo rankeado por |correlación| con el ticker
                            — ratio de cobertura, notional y acción por candidato,
          nota:             qué falta (costo del short, escenarios).
        }

    Pendiente (ver doc): escenarios de stress y niveles de entrada/salida.
    """
    from api.services.scanner import get_quant_stats

    tk = ticker.strip().upper()
    es_long = direccion.strip().lower() != "short"

    qs = get_quant_stats(ticker=tk)
    vol30, vol60 = qs["vol"]["d30"], qs["vol"]["d60"]
    beta_spy, beta_qqq = qs["beta"]["spy"], qs["beta"]["qqq"]

    var_1d = monto * _Z95 * vol60 / _SQRT252 if vol60 else None
    peor_mes = monto * vol60 / _SQRT12 if vol60 else None

    caracterizacion = {
        "last":      qs.get("last"),
        "vol_anual": {"d30": vol30, "d60": vol60},
        "beta":      {"spy": beta_spy, "qqq": beta_qqq},
        "zscore":    qs.get("zscore"),
        "var_1d_95":      round(var_1d, 0) if var_1d is not None else None,
        "var_1d_95_pct":  round(_Z95 * vol60 / _SQRT252 * 100, 2) if vol60 else None,
        "peor_mes_1sigma": round(peor_mes, 0) if peor_mes is not None else None,
        "exposicion_mercado_equiv": {
            "spy": round(monto * beta_spy, 0) if beta_spy is not None else None,
            "qqq": round(monto * beta_qqq, 0) if beta_qqq is not None else None,
        },
    }

    # Hedge directo por beta (vs SPY/QQQ): neutraliza el riesgo de mercado.
    hedge_beta = []
    for bench, b in (("SPY", beta_spy), ("QQQ", beta_qqq)):
        if b is None:
            continue
        hedge_beta.append({
            "benchmark": bench,
            "beta":      round(b, 3),
            "accion":    "short" if es_long else "long",
            "notional":  round(monto * b, 0),
        })

    # Hedge-finder: universo rankeado por correlación con el ticker.
    # Ratio de mínima varianza: h = ρ × σ_ticker / σ_otro.
    # Reducción de vol de la cobertura óptima ∝ 1 − √(1 − ρ²).
    m = get_correlation_matrix()
    hedge_finder: list[dict] = []
    if tk in m["tickers"]:
        idx = m["tickers"].index(tk)
        fila = m["matriz"][idx]
        vols = m["vol_anual"]
        sigma_t = vols.get(tk)
        for j, otro in enumerate(m["tickers"]):
            if otro == tk:
                continue
            rho = fila[j]
            if rho is None:
                continue
            sigma_o = vols.get(otro)
            hr = rho * sigma_t / sigma_o if (sigma_t and sigma_o) else None
            # Long trade: se cubre shorteando lo +corr / longueando lo −corr.
            # Para un short, al revés.
            cubre_shorteando = rho > 0
            if not es_long:
                cubre_shorteando = not cubre_shorteando
            hedge_finder.append({
                "ticker":            otro,
                "correlacion":       round(rho, 3),
                "hedge_ratio":       round(hr, 3) if hr is not None else None,
                "notional_hedge":    round(monto * abs(hr), 0) if hr is not None else None,
                "accion":            "short" if cubre_shorteando else "long",
                "reduccion_vol_pct": round((1 - (1 - rho ** 2) ** 0.5) * 100, 1),
            })
        hedge_finder.sort(key=lambda c: abs(c["correlacion"]), reverse=True)

    return {
        "trade": {
            "ticker":    tk,
            "monto":     monto,
            "direccion": "long" if es_long else "short",
        },
        "caracterizacion": caracterizacion,
        "hedge_beta":      hedge_beta,
        "hedge_finder":    hedge_finder,
        "nota": (
            "Escenarios de stress y niveles de entrada/salida (pivots) "
            "pendientes."
        ),
    }


# ── Módulo 2 — análisis de book / exposición ────────────────────────────────


def _master_info() -> dict[str, dict]:
    """{ticker_corto: {underlying, sector, region, pais}} de Trading.Cedears."""
    db = get_db_trading()
    out: dict[str, dict] = {}
    for d in db["Cedears"].find(
        {}, {"_id": 0, "ticker_corto": 1, "underlying": 1,
             "sector": 1, "region": 1, "pais": 1},
    ):
        tc = d.get("ticker_corto")
        if tc:
            out[tc.upper()] = {
                "underlying": (d.get("underlying") or tc).upper(),
                "sector":     d.get("sector") or "—",
                "region":     d.get("region") or "—",
                "pais":       d.get("pais") or "—",
            }
    return out


def get_book_analysis(posiciones: tuple[tuple[str, float], ...]) -> dict:
    """Analiza un book entero — Módulo 2 de la Mesa de Estrategia.

    Args:
        posiciones: tuple de (ticker_corto, notional_usd). notional > 0 = long,
            notional < 0 = short. Se consolida por ticker.

    Returns:
        book (gross/net/posiciones), exposición por sector y región,
        concentración (top, %top5, HHI), riesgo agregado (vol del book, VaR
        1d, beta en USD = exposición de mercado equivalente) y la
        contribución de riesgo de cada posición.

    Pendiente (ver doc): stress test, alertas, rebalanceo, conexión a AuM.
    """
    from api.services.scanner import get_quant_stats

    consol: dict[str, float] = {}
    for tk, notional in posiciones:
        t = str(tk).strip().upper()
        if t:
            consol[t] = consol.get(t, 0.0) + float(notional)
    consol = {t: n for t, n in consol.items() if n}

    if not consol:
        return {
            "book": {"posiciones": [], "gross": 0, "net": 0, "n": 0},
            "exposicion": {"por_sector": [], "por_region": []},
            "concentracion": {}, "riesgo": {}, "contribucion_riesgo": [],
            "excluidos": [], "nota": "Book vacío.",
        }

    master = _master_info()
    gross = sum(abs(n) for n in consol.values())
    net = sum(consol.values())

    posiciones_out = sorted(
        [
            {
                "ticker":    t,
                "notional":  round(n, 0),
                "direccion": "long" if n > 0 else "short",
                "sector":    master.get(t, {}).get("sector", "—"),
                "region":    master.get(t, {}).get("region", "—"),
            }
            for t, n in consol.items()
        ],
        key=lambda p: abs(p["notional"]), reverse=True,
    )

    # Exposición agregada por campo categórico.
    def _agg(campo: str) -> list[dict]:
        g: dict[str, dict] = {}
        for p in posiciones_out:
            d = g.setdefault(p[campo], {"grupo": p[campo], "neto": 0.0, "bruto": 0.0})
            d["neto"] += p["notional"]
            d["bruto"] += abs(p["notional"])
        filas = sorted(g.values(), key=lambda d: d["bruto"], reverse=True)
        for d in filas:
            d["neto"] = round(d["neto"], 0)
            d["bruto"] = round(d["bruto"], 0)
            d["pct_bruto"] = round(d["bruto"] / gross * 100, 1) if gross else 0
        return filas

    concentracion = {
        "pct_top5": round(
            sum(abs(p["notional"]) for p in posiciones_out[:5]) / gross * 100, 1,
        ) if gross else 0,
        "hhi": round(
            sum((abs(p["notional"]) / gross) ** 2 for p in posiciones_out) * 100, 1,
        ) if gross else 0,
    }

    # Riesgo agregado — necesita la matriz de correlación del book.
    m = get_correlation_matrix(tickers=tuple(sorted(consol.keys())))
    idx = {t: i for i, t in enumerate(m["tickers"])}
    riesgo_tk = [t for t in consol if t in idx]
    excluidos = sorted(t for t in consol if t not in idx)

    riesgo: dict = {}
    contribucion: list[dict] = []
    if riesgo_tk:
        sd = {t: (m["vol_anual"].get(t) or 0.0) / _SQRT252 for t in riesgo_tk}
        # Var diaria del P&L = ΣΣ n_i n_j corr_ij σd_i σd_j.
        # Contribución_i = n_i × Σ_j n_j corr_ij σd_i σd_j  (suma = var).
        contrib_raw: dict[str, float] = {}
        var_pl = 0.0
        for ti in riesgo_tk:
            fila = m["matriz"][idx[ti]]
            s_i = 0.0
            for tj in riesgo_tk:
                rho = fila[idx[tj]]
                if rho is not None:
                    s_i += consol[tj] * rho * sd[ti] * sd[tj]
            contrib_raw[ti] = consol[ti] * s_i
            var_pl += consol[ti] * s_i

        sigma_pl_d = var_pl ** 0.5 if var_pl > 0 else 0.0

        # Beta en USD: Σ n_i × β_i = exposición de mercado equivalente.
        beta_usd_spy = beta_usd_qqq = 0.0
        for t in riesgo_tk:
            qs = get_quant_stats(ticker=t)
            if qs["beta"]["spy"] is not None:
                beta_usd_spy += consol[t] * qs["beta"]["spy"]
            if qs["beta"]["qqq"] is not None:
                beta_usd_qqq += consol[t] * qs["beta"]["qqq"]

        riesgo = {
            "vol_anual_book_pct": (
                round(sigma_pl_d * _SQRT252 / gross * 100, 1) if gross else None
            ),
            "var_1d_95": round(_Z95 * sigma_pl_d, 0),
            "exposicion_mercado_usd": {
                "spy": round(beta_usd_spy, 0),
                "qqq": round(beta_usd_qqq, 0),
            },
            "n_obs": m["n_obs"],
        }
        if var_pl > 0:
            contribucion = sorted(
                [
                    {
                        "ticker":       t,
                        "notional":     round(consol[t], 0),
                        "contrib_pct":  round(contrib_raw[t] / var_pl * 100, 1),
                    }
                    for t in riesgo_tk
                ],
                key=lambda c: c["contrib_pct"], reverse=True,
            )

    return {
        "book": {
            "posiciones": posiciones_out,
            "gross": round(gross, 0),
            "net":   round(net, 0),
            "n":     len(posiciones_out),
        },
        "exposicion": {"por_sector": _agg("sector"), "por_region": _agg("region")},
        "concentracion": concentracion,
        "riesgo": riesgo,
        "contribucion_riesgo": contribucion,
        "excluidos": excluidos,
        "nota": (
            "Stress test, alertas, rebalanceo y conexión a AuM pendientes."
        ),
    }
