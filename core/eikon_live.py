"""Feed Eikon/Workspace — quotes LIVE del subyacente US de cada CEDEAR (PRUEBA).

Mismo patrón que el dólar oficial MAE (`core/dolar_oficial.py`): un script local
en la PC de la oficina (`scripts/eikon_feed.py`, necesita Workspace abierto y
logueado) pollea Eikon y le pega a `POST /api/ingest/eikon/quotes`; la API (este
módulo) persiste en SQL `mercado.eikon_snapshot`. La PC NO toca la base directo.

Es un camino SEPARADO de `mercado.adr_snapshot` (Finnhub cada 15 min): conviven
y de momento nadie lee esta tabla — primero validamos que el dato llegue bien.

El RIC (identidad Refinitiv del subyacente, ej. AAPL.O) vive en
`mercado.cedears.ric`. El feed lo resuelve solo (symbology de Eikon) para los
underlyings que no lo tengan y lo persiste acá vía `set_rics` — que SOLO llena
vacíos, nunca pisa un RIC ya cargado (ej. los de RESEARCH, `scripts/set_ric.py`).
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
