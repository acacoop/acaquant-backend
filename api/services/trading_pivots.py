"""Pivots Floor Trader sobre el activo (ARS) — vista TRADING.

Soporta DOS clases de activo, resueltas por su master:
  • CEDEARs → OHLC diario de mercado.cedears_ohlc_daily (jobs.cedears_ohlc_daily),
    last live del scanner (mercado.cedears_snapshot).
  • Bonos (renta fija, mercado.curvas) → OHLC diario de mercado.bonos_ohlc_daily
    (jobs.bonos_ohlc_daily), last live de mercado.market_snapshot.

Las dos tablas de OHLC tienen el MISMO shape (ticker_corto, fecha, high, low,
close) → el cálculo de los 7 niveles (PP, R1-R3, S1-S3) con
`quant.pivot_points.calcular` es idéntico. La única diferencia es de qué tabla
sale el último cierre guardado y de dónde el `last` live.

Si la tabla todavía no tiene una rueda para un ticker → {sin_datos: true} (las
tablas se llenan hacia adelante: ver [[project_vista_trading]]). Con el bono
igual llega el `last` live, así la card muestra precio desde el día 0.
"""
from __future__ import annotations

import logging

from psycopg.rows import dict_row

from api.cache import cached
from api.services import scanner_sql as scanner_svc
from core import curvas_sql, market_snapshot
from core.postgres import get_pool
from quant.pivot_points import calcular

logger = logging.getLogger(__name__)


def _ohlc_ultima_rueda(tabla: str, tickers: list[str]) -> dict[str, dict]:
    """{ticker_corto: {fecha, high, low, close}} de la rueda más reciente guardada.
    `tabla` es un literal interno (cedears/bonos), NO input de usuario."""
    if not tickers:
        return {}
    try:
        with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT DISTINCT ON (ticker_corto)
                       ticker_corto, fecha, high, low, close
                FROM {tabla}
                WHERE ticker_corto = ANY(%s)
                ORDER BY ticker_corto, fecha DESC
                """,
                (tickers,),
            )
            return {r["ticker_corto"]: r for r in cur.fetchall()}
    except Exception as e:
        # La tabla se crea hacia adelante (DDL del job en su primer run). Si aún no
        # existe → sin OHLC guardado = sin_datos (el `last` live igual llega aparte).
        logger.debug("trading_pivots: %s no disponible (%s)", tabla, e)
        return {}


def _cedears_live(tickers: set[str]) -> dict[str, dict]:
    """{ticker_corto: {last, vwap}} live de cada CEDEAR desde el scanner (1 llamada, @cached)."""
    try:
        universo = scanner_svc.get_cedears_scanner()
    except Exception as e:
        logger.debug("trading_pivots: scanner falló (%s)", e)
        return {}
    out: dict[str, dict] = {}
    for r in universo:
        tk = str(r.get("ticker_corto", "")).upper()
        if tk in tickers:
            out[tk] = {"last": r.get("last"), "vwap": r.get("vwap")}
    return out


def _bonos_corto_a_largo() -> dict[str, str]:
    """{ticker_corto(upper): ticker_largo ROFEX} del master de renta fija (cacheado 300s)."""
    out: dict[str, str] = {}
    for d in curvas_sql.cargar_todos():
        tc, tk = d.get("ticker_corto"), d.get("ticker")
        if tc and tk:
            out[str(tc).upper()] = tk
    return out


def _bonos_live(tickers: list[str], corto_a_largo: dict[str, str]) -> dict[str, dict]:
    """{ticker_corto: {last, vwap}} live de cada bono desde mercado.market_snapshot
    (keyeado por ticker largo)."""
    largos = [corto_a_largo[t] for t in tickers if t in corto_a_largo]
    lp = market_snapshot.metric_map(largos, "last_price", positivo=True)  # {largo: last}
    vw = market_snapshot.metric_map(largos, "vwap", positivo=True)        # {largo: vwap}
    out: dict[str, dict] = {}
    for t in tickers:
        largo = corto_a_largo.get(t)
        if largo is not None:
            out[t] = {"last": lp.get(largo), "vwap": vw.get(largo)}
    return out


_CURVA_LABEL = {"tasa_fija": "Tasa Fija", "cer": "CER", "soberanos": "Soberano"}


def _curva_label(curva: str | None) -> str:
    """Etiqueta corta de la curva para el selector (AL30 → 'Soberano')."""
    if not curva:
        return "Bono"
    if curva.startswith("on"):
        return "ON"
    return _CURVA_LABEL.get(curva, curva.replace("_", " ").title())


def bonos_universo() -> list[dict]:
    """Catálogo liviano de bonos de renta fija (mercado.curvas) para el selector.
    Una fila por bono: {ticker_corto, nombre (curva), clase='bono'}."""
    out: list[dict] = []
    for d in curvas_sql.cargar_todos():
        tc = d.get("ticker_corto")
        if not tc:
            continue
        out.append({
            "ticker_corto": tc,
            "nombre": _curva_label(d.get("curva")),
            "clase": "bono",
        })
    out.sort(key=lambda x: x["ticker_corto"] or "")
    return out


def get_pivots(*, tickers: list[str]) -> list[dict]:
    """Pivots por ticker (orden pedido). last live + niveles sobre la última rueda
    guardada. Resuelve CEDEAR vs bono por el master de renta fija."""
    tks = [t.strip().upper() for t in (tickers or []) if t and t.strip()]
    if not tks:
        return []

    corto_a_largo = _bonos_corto_a_largo()
    bonos = [t for t in tks if t in corto_a_largo]
    cedears = [t for t in tks if t not in corto_a_largo]

    ohlc: dict[str, dict] = {}
    live: dict[str, dict] = {}
    if cedears:
        ohlc.update(_ohlc_ultima_rueda("mercado.cedears_ohlc_daily", cedears))
        live.update(_cedears_live(set(cedears)))
    if bonos:
        ohlc.update(_ohlc_ultima_rueda("mercado.bonos_ohlc_daily", bonos))
        live.update(_bonos_live(bonos, corto_a_largo))

    out: list[dict] = []
    for tk in tks:
        info = live.get(tk) or {}
        last, vwap = info.get("last"), info.get("vwap")
        row = ohlc.get(tk)
        if not row:
            out.append({"ticker": tk, "last": last, "vwap": vwap, "sin_datos": True})
            continue
        h, l, c = float(row["high"]), float(row["low"]), float(row["close"])
        niveles = calcular(high=h, low=l, close=c)
        fecha = row.get("fecha")
        out.append({
            "ticker": tk,
            "fecha": fecha.isoformat() if hasattr(fecha, "isoformat") else None,
            "high": h, "low": l, "close": c,
            "last": last,
            "vwap": vwap,
            "pivots": dict(niveles),  # pp, r1, r2, r3, s1, s2, s3
        })
    return out


def get_trades(*, ticker: str, limite: int = 200) -> list[dict]:
    """Time & Sales (tape) del activo, resolviendo la fuente por clase — MISMO
    criterio que get_pivots (CEDEAR vs bono por el master de renta fija):
      • CEDEAR → mercado.cedears_time_sales (tape inferido por motor_cedears).
      • Bono   → mercado.timesales (trades del día del motor de curvas, la misma
        fuente que el tape de la vista Renta Fija).
    Shape unificado (el que espera el panel): [{timestamp, price, size, side,
    money}] desc por ts. `ticker` = ticker_corto (ej 'AL30' / 'NVDA')."""
    tk = (ticker or "").strip().upper()
    if not tk:
        return []
    if tk in _bonos_corto_a_largo():
        # Bono: el tape vive en mercado.timesales (RF). get_historico_trades
        # resuelve el ticker_corto → largo ROFEX y filtra al día de hoy.
        from api.services import renta_fija_sql
        filas = renta_fija_sql.get_historico_trades(instrumento=tk) or []
        return [
            {"timestamp": r.get("timestamp"), "price": r.get("price"),
             "size": r.get("size"), "side": r.get("side"), "money": r.get("money")}
            for r in filas[:limite]
        ]
    return scanner_svc.get_cedears_trades(ticker=tk, limite=limite)


def get_intraday(*, ticker: str) -> list[dict]:
    """Serie intradía por minuto (OHLC) del activo para el chart LIVE, resolviendo
    la fuente por clase igual que get_pivots/get_trades:
      • CEDEAR → mercado.cedears_time_sales (scanner).
      • Bono   → agrega los trades de HOY de mercado.timesales por minuto (el
        chart solo usa t + close; se arma OHLC para el mismo shape).
    Shape: [{t, o, h, l, c, vol}] asc por minuto. `t` naive ART (== ts de la
    tabla) → el browser lo lee en hora local, igual que el tape de bonos."""
    tk = (ticker or "").strip().upper()
    if not tk:
        return []
    if tk not in _bonos_corto_a_largo():
        return scanner_svc.get_cedears_intraday(ticker=tk)

    from api.services import renta_fija_sql
    trades = renta_fija_sql.get_historico_trades(instrumento=tk) or []
    por_min: dict[str, dict] = {}
    for tr in reversed(trades):  # trades vienen desc → asc para que `c` sea el último
        ts = tr.get("timestamp")
        px = tr.get("price")
        if ts is None or px is None:
            continue
        key = ts.strftime("%Y-%m-%dT%H:%M:00")
        sz = tr.get("size") or 0.0
        b = por_min.get(key)
        if b is None:
            por_min[key] = {"t": key, "o": px, "h": px, "l": px, "c": px, "vol": sz}
        else:
            b["h"] = max(b["h"], px)
            b["l"] = min(b["l"], px)
            b["c"] = px
            b["vol"] = (b["vol"] or 0.0) + sz
    return [por_min[k] for k in sorted(por_min)]


@cached(ttl=4)
def get_renta_fija_radar() -> list[dict]:
    """Radar de RENTA FIJA (vista TRADING): bonos en PESOS suscriptos (tasa fija
    + CER) con last, TNA y volumen del día, ordenados por volumen desc — para el
    tab RENTA FIJA del panel de movers (click → carga la card). Reusa
    renta_fija_sql.listar_curva (el MISMO ensamblado live de la vista RF). TNA =
    TEM × 12 (nominal anual). Incluye todos los suscriptos; los que no operaron
    quedan al fondo (volumen 0) y muestran '—' donde no hay dato."""
    from api.services import renta_fija_sql

    out: list[dict] = []
    vistos: set[str] = set()
    for curva in ("tasa_fija", "cer"):
        for b in renta_fija_sql.listar_curva(curva=curva, ordenar_por="volumen_dia"):
            tk = b.get("ticker_corto")
            if not tk or tk in vistos:
                continue
            vistos.add(tk)
            tem = b.get("tem")
            out.append({
                "ticker_corto": tk,
                "last": b.get("ultimo_precio"),
                "tea": b.get("tea"),  # los CER cotizan en TEA (no tienen TNA)
                "tna": tem * 12 if tem is not None else None,
                "volumen": b.get("total_nominals_dia"),
            })
    out.sort(key=lambda x: -(x.get("volumen") or 0))
    return out


@cached(ttl=2)
def pivot_radar() -> list[dict]:
    """Radar de proximidad a pivote sobre TODO el universo de CEDEARs.

    Para cada CEDEAR con last live + pivots (última rueda guardada), calcula el
    nivel de pivote MÁS CERCANO y la distancia % del last a ese nivel. Devuelve
    TODOS los que tienen dato, ordenados por distancia absoluta asc — el frontend
    filtra por el umbral elegido (el selector no re-pega al backend).

    Cada item: {ticker, last, nivel ('PP'|'R1'..'S3'), nivel_precio,
    dist_pct (signed: + = last por encima del nivel)}.
    """
    universo = [
        str(u.get("ticker_corto", "")).upper()
        for u in scanner_svc.get_universo()
        if u.get("ticker_corto")
    ]
    out: list[dict] = []
    for r in get_pivots(tickers=universo):
        last = r.get("last")
        pivots = r.get("pivots")
        if not last or last <= 0 or not pivots:
            continue
        # Nivel más cercano por distancia relativa (denominador = last).
        best_key, best_val, best_dist = None, None, None
        for key, val in pivots.items():
            if val is None:
                continue
            dist = (last - val) / last * 100.0
            if best_dist is None or abs(dist) < abs(best_dist):
                best_key, best_val, best_dist = key, val, dist
        if best_key is None:
            continue
        out.append({
            "ticker": r["ticker"],
            "last": last,
            "nivel": best_key.upper(),        # pp/r1/… → PP/R1/…
            "nivel_precio": best_val,
            "dist_pct": best_dist,
        })
    out.sort(key=lambda d: abs(d["dist_pct"]))
    return out
