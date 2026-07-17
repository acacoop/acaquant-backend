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


def tablero_reuters() -> list[dict]:
    """Filas del tablero TRADING → REUTERS: un activo por fila, SOLO los que el
    feed fue suscribiendo (los que tienen quote en `mercado.eikon_snapshot`),
    con el ratio del CEDEAR del catálogo. `ccl` va None hasta que se implemente
    el cálculo en vivo (precio_cedear × ratio / precio_adr)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT e.ticker, e.ric, e.data, e.updated_at, c.ratio "
            "FROM mercado.eikon_snapshot e "
            "LEFT JOIN LATERAL ("
            "  SELECT max(ratio) AS ratio FROM mercado.cedears "
            "  WHERE upper(COALESCE(underlying, ticker_corto)) = e.ticker "
            "    AND activo IS TRUE"
            ") c ON TRUE "
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
        for ticker, ric, data, updated_at, ratio in cur.fetchall():
            d = data or {}
            fila = {"ticker": ticker, "ric": ric}
            fila.update({c: d.get(c) for c in campos})
            fila["ah_var_pct"] = _var_pct(d.get("ah_last"), d.get("last"))
            fila["pre_var_pct"] = _var_pct(d.get("pre_last"), d.get("prev_close"))
            fila["ratio"] = float(ratio) if ratio is not None else None
            fila["ccl"] = None             # pendiente: cedear_ars × ratio / adr_usd
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
