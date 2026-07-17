"""Feed Eikon/Workspace — quotes LIVE del subyacente US de cada CEDEAR (PRUEBA).

Mismo patrón que el dólar oficial MAE (`core/dolar_oficial.py`): un script local
en la PC de la oficina (`scripts/eikon_feed_simple.py`, necesita Workspace abierto y
logueado) pollea Eikon y le pega a `POST /api/ingest/eikon/quotes`; la API (este
módulo) persiste en SQL `mercado.eikon_snapshot`. La PC NO toca la base directo.

Es un camino SEPARADO de `mercado.adr_snapshot` (Finnhub cada 15 min): conviven
y de momento nadie lee esta tabla — primero validamos que el dato llegue bien.

El RIC (identidad Refinitiv del subyacente, ej. AAPL.O) vive en
`mercado.cedears.ric` y se carga A MANO desde Manager → TÍTULOS → RENTA VARIABLE
(o `scripts/set_ric.py`). El feed solo LEE los cargados; los sin RIC quedan fuera.
(`set_rics` queda disponible para una futura resolución asistida — solo llena
vacíos, nunca pisa una carga manual. La auto-resolución del feed se quitó
2026-07-16: la symbology devolvía tickers pelados para NYSE y ensució el catálogo.)
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.pg_mirror import write_native
from core.postgres import get_pool

TABLE = "mercado.eikon_snapshot"


def universo_rics() -> list[dict]:
    """Underlyings (US symbol) únicos de los CEDEARs activos + su RIC si lo hay.

    Lo consume `GET /api/ingest/eikon/universo`: es lo que el feed local usa
    como lista de suscripción (los que tienen `ric=None` los resuelve él).
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT upper(COALESCE(underlying, ticker_corto)) AS und, "
            "       max(NULLIF(ric, ''))                      AS ric "
            "FROM mercado.cedears WHERE activo IS TRUE "
            "GROUP BY 1 ORDER BY 1"
        )
        return [{"ticker": t, "ric": r} for t, r in cur.fetchall()]


def set_rics(items: list[dict]) -> int:
    """Persiste RICs resueltos por el feed en `mercado.cedears.ric`.

    Cada item: {ticker: underlying US, ric: 'AAPL.O'}. Solo llena filas SIN ric
    (no pisa cargas manuales). Devuelve cuántos underlyings se actualizaron.
    """
    actualizados = 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        for item in items:
            ticker = (item.get("ticker") or "").strip().upper()
            ric = (item.get("ric") or "").strip()
            if not ticker or not ric:
                continue
            cur.execute(
                "UPDATE mercado.cedears SET ric = %s "
                "WHERE upper(COALESCE(underlying, ticker_corto)) = %s "
                "  AND (ric IS NULL OR ric = '')",
                (ric, ticker),
            )
            if cur.rowcount:
                actualizados += 1
    return actualizados


def _ccl_implicito(cedear_data, cedear_upd, ratio, adr_last) -> float | None:
    """CCL implícito del papel: (last del CEDEAR en ARS × ratio) ÷ last del ADR
    en USD. REGLA: nunca rompe — si falta CUALQUIER pata (feed Eikon apagado,
    CEDEAR sin operar, sin ratio cargado, dato local que no es de HOY), devuelve
    None y la celda queda vacía."""
    try:
        if not cedear_data or ratio is None or adr_last is None:
            return None
        r = float(ratio)
        adr = float(adr_last)
        cedear = float(cedear_data.get("last") or 0)
        if r <= 0 or adr <= 0 or cedear <= 0:
            return None
        # El last del CEDEAR debe ser de HOY (ART): mezclar un ARS viejo con un
        # USD fresco fabrica un CCL falso — mejor celda vacía.
        if cedear_upd is None:
            return None
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Argentina/Buenos_Aires")
        if cedear_upd.astimezone(tz).date() != datetime.now(tz).date():
            return None
        return cedear * r / adr
    except (TypeError, ValueError, AttributeError):
        return None


def tablero_reuters() -> list[dict]:
    """Filas del tablero TRADING → REUTERS: un activo por fila, SOLO los que el
    feed fue suscribiendo (los que tienen quote en `mercado.eikon_snapshot`),
    con el ratio del CEDEAR del catálogo y el CCL implícito calculado
    SERVER-SIDE (last CEDEAR ARS × ratio ÷ last ADR USD, ver _ccl_implicito)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT e.ticker, e.ric, e.data, e.updated_at, c.ratio, "
            "       cs.data AS cedear_data, cs.updated_at AS cedear_upd "
            "FROM mercado.eikon_snapshot e "
            "LEFT JOIN LATERAL ("
            "  SELECT m.ticker AS cedear_ticker, m.ratio FROM mercado.cedears m "
            "  WHERE upper(COALESCE(m.underlying, m.ticker_corto)) = e.ticker "
            "    AND m.activo IS TRUE "
            "  ORDER BY m.ratio IS NULL, m.ticker LIMIT 1"
            ") c ON TRUE "
            "LEFT JOIN mercado.cedears_snapshot cs ON cs.ticker = c.cedear_ticker "
            "ORDER BY e.ticker")
        # sin "open": CF_OPEN no viene para equities US (verificado 2026-07-16,
        # 27/27 suscriptos sin el dato) — se quitó del feed y de la vista
        campos = [
            "last", "bid", "ask", "high", "low", "prev_close", "volumen",
            "var_pct", "var_neta", "ah_last", "ah_vol", "pre_last",
            "eod_close", "eod_open", "eod_high", "eod_low", "eod_vol",
            "ret_1d", "ret_5d", "ret_wtd", "ret_mtd", "ret_qtd", "ret_ytd",
            "ret_1m", "ret_3m", "ret_1y", "ret_5y",
        ]
        filas = []
        for ticker, ric, data, updated_at, ratio, cedear_data, cedear_upd in cur.fetchall():
            d = data or {}
            fila = {"ticker": ticker, "ric": ric}
            fila.update({c: d.get(c) for c in campos})
            fila["ah_var_pct"] = _var_pct(d.get("ah_last"), d.get("last"))
            fila["pre_var_pct"] = _var_pct(d.get("pre_last"), d.get("prev_close"))
            fila["ratio"] = float(ratio) if ratio is not None else None
            fila["ccl"] = _ccl_implicito(cedear_data, cedear_upd, ratio, d.get("last"))
            fila["updated_at"] = updated_at
            filas.append(fila)
        return filas


def _var_pct(precio, base) -> float | None:
    """Variación % de `precio` contra `base` (after vs cierre de hoy, pre vs
    cierre anterior). None si falta alguno o la base es 0."""
    try:
        if precio is None or base is None or not float(base):
            return None
        return (float(precio) / float(base) - 1.0) * 100.0
    except (TypeError, ValueError):
        return None


def tablero_fundamentals() -> list[dict]:
    """Filas del screener FUNDAMENTALS del tab REUTERS: una empresa por fila con
    todas las métricas de la ficha (sin las series históricas, que son pesadas
    y viven en la ficha). Ordenado por ticker."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, ric, data, updated_at "
                    "FROM mercado.eikon_fundamentals ORDER BY ticker")
        filas = []
        for ticker, ric, data, updated_at in cur.fetchall():
            d = {k: v for k, v in (data or {}).items()
                 if k not in ("serie_anual", "serie_trimestral")}
            d["ticker"] = ticker
            d["ric"] = ric
            d["updated_at"] = updated_at
            filas.append(d)
        return filas


def upsert_fundamentals(docs: list[dict]) -> int:
    """Upsertea los fundamentals curados del feed en `mercado.eikon_fundamentals`
    (1 fila por ticker; el feed los manda ~1 vez por día). Mismo contrato que
    upsert_quotes: `updated_at` lo pone el server."""
    if not docs:
        return 0
    now = datetime.now(UTC)
    rows: list[dict] = []
    for data in docs:
        if not isinstance(data, dict):
            continue
        ticker = (data.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        rows.append({
            "ticker":     ticker,
            "ric":        data.get("ric"),
            "data":       data,
            "updated_at": now,
        })
    if not rows:
        return 0
    return write_native("mercado.eikon_fundamentals", ["ticker"], rows)


def ficha(ticker: str) -> dict | None:
    """La FICHA de una empresa del tab REUTERS: quote live (eikon_snapshot) +
    fundamentals curados (eikon_fundamentals) + ratio del CEDEAR + velas
    diarias de 1 año (mercado.precios_acciones, EOD ya en casa) para el chart.
    None si el ticker no existe en ninguna fuente (→ 404)."""
    t = (ticker or "").strip().upper()
    if not t:
        return None
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ric, data, updated_at FROM mercado.eikon_snapshot "
                    "WHERE ticker = %s", (t,))
        q = cur.fetchone()
        cur.execute("SELECT ric, data, updated_at FROM mercado.eikon_fundamentals "
                    "WHERE ticker = %s", (t,))
        f = cur.fetchone()
        cur.execute("SELECT max(ratio) FROM mercado.cedears "
                    "WHERE upper(COALESCE(underlying, ticker_corto)) = %s "
                    "  AND activo IS TRUE", (t,))
        ratio = (cur.fetchone() or [None])[0]
        cur.execute("SELECT fecha, close FROM mercado.precios_acciones "
                    "WHERE ticker = %s AND close IS NOT NULL "
                    "  AND fecha >= CURRENT_DATE - 380 ORDER BY fecha", (t,))
        velas = [{"fecha": fe.isoformat(), "close": float(cl)} for fe, cl in cur.fetchall()]

    if q is None and f is None and not velas:
        return None
    quote = dict(q[1] or {}) if q else {}
    if q:
        quote["updated_at"] = q[2].isoformat() if q[2] else None
        quote["ah_var_pct"] = _var_pct(quote.get("ah_last"), quote.get("last"))
        quote["pre_var_pct"] = _var_pct(quote.get("pre_last"), quote.get("prev_close"))
    fund = dict(f[1] or {}) if f else None
    if fund is not None and f:
        fund["updated_at"] = f[2].isoformat() if f[2] else None
    return {
        "ticker": t,
        "ric": (q and q[0]) or (f and f[0]),
        "ratio": float(ratio) if ratio is not None else None,
        "quote": quote or None,
        "fundamentals": fund,
        "velas": velas,
    }


def upsert_quotes(docs: list[dict]) -> int:
    """Upsertea quotes del feed en `mercado.eikon_snapshot` (1 fila por ticker,
    no acumula histórico). `updated_at` lo pone el server (no se confía en el
    reloj de la PC de oficina). Devuelve cuántos se escribieron."""
    if not docs:
        return 0
    now = datetime.now(UTC)
    rows: list[dict] = []
    for data in docs:
        if not isinstance(data, dict):
            continue
        ticker = (data.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        rows.append({
            "ticker":     ticker,
            "ric":        data.get("ric"),
            "data":       data,          # dict → jsonb passthrough
            "updated_at": now,
        })
    if not rows:
        return 0
    return write_native(TABLE, ["ticker"], rows)
