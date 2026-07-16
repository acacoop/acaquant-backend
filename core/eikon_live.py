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
        filas = []
        for ticker, ric, data, updated_at, ratio in cur.fetchall():
            d = data or {}
            filas.append({
                "ticker":     ticker,
                "ric":        ric,
                "last":       d.get("last"),
                "bid":        d.get("bid"),
                "ask":        d.get("ask"),
                "open":       d.get("open"),
                "high":       d.get("high"),
                "low":        d.get("low"),
                "prev_close": d.get("prev_close"),
                "volumen":    d.get("volumen"),
                "var_pct":    d.get("var_pct"),
                "var_neta":   d.get("var_neta"),
                "ratio":      float(ratio) if ratio is not None else None,
                "ccl":        None,        # pendiente: cedear_ars × ratio / adr_usd
                "updated_at": updated_at,
            })
        return filas


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
