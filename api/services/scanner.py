"""api/services/scanner.py — vista Scanner del módulo Renta Variable.

SQL-NATIVE (decomiso Mongo): `Trading.{Cedears,CedearsSnapshot,PreciosAcciones,AdrSnapshot}`
fueron migradas a `mercado.*` y dropeadas. El master/ADR/returns/pivots/quant/scanner
viven en `scanner_sql` (lee Postgres) y los call-sites lo invocan directo.

Acá quedan las 3 lecturas que NO están en `scanner_sql` (él las reusa desde este
módulo): `get_ccl_live` (dolar_sql) + `get_cedears_trades` / `get_cedears_intraday`
(mercado.cedears_time_sales).

Retorno USD (`vs_1d_usd_pct`): los CEDEARs se mueven en ARS pero el underlying es un
activo USD — parte del movimiento ARS es la devaluación implícita del CCL.
"""
from __future__ import annotations

from datetime import UTC, datetime

from psycopg.rows import dict_row

from api.cache import cached
from core.postgres import get_pool

# Si valuaciones.dolar (snapshot live) tiene timestamp más viejo que esto, lo
# consideramos stale y caemos al último valuaciones.dolar. Mismo valor que argy.
_DOLAR_SNAPSHOT_MAX_AGE_S = 60


@cached(ttl=5)
def get_ccl_live() -> dict:
    """CCL live + variación 1D vs cierre día previo (SQL-native: core.dolar_sql).

    Compartido entre `/api/scanner/ccl` y `get_cedears_scanner()`. Returns
    {value, vs_1d_pct, ts}; cualquier campo None si no hay live ni cierre previo.
    """
    from core import dolar_sql

    ccl_value: float | None = None
    ts: datetime | None = None

    snap = dolar_sql.snapshot_live()
    if snap:
        snap_ts = snap.get("timestamp")
        if isinstance(snap_ts, datetime):
            age = (datetime.now(snap_ts.tzinfo) - snap_ts).total_seconds()
            if age <= _DOLAR_SNAPSHOT_MAX_AGE_S and snap.get("ccl") is not None:
                ccl_value = float(snap["ccl"])
                ts = snap_ts

    if ccl_value is None:
        latest = dolar_sql.ultimo("ccl")
        if latest and latest.get("ccl"):
            ccl_value = float(latest["ccl"])
            ts = latest.get("timestamp")

    if not ccl_value or ccl_value <= 0:
        return {"value": None, "vs_1d_pct": None, "ts": None}

    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    prev = dolar_sql.ultimo("ccl", antes_de=today_start)
    vs_1d_pct: float | None = None
    if prev and prev.get("ccl"):
        ccl_prev = float(prev["ccl"])
        if ccl_prev > 0:
            vs_1d_pct = (ccl_value / ccl_prev - 1) * 100

    return {
        "value":     ccl_value,
        "vs_1d_pct": vs_1d_pct,
        "ts":        ts.isoformat() if isinstance(ts, datetime) else None,
    }


# ── Time & Sales intradía — SQL-native (mercado.cedears_time_sales) ───────────

def get_cedears_trades(*, ticker: str, limite: int = 200) -> list[dict]:
    """Time & Sales intradía de un CEDEAR (tape). Lee mercado.cedears_time_sales
    (SQL-native; la tabla se vacía al cierre), filtrado a la sesión de hoy, desc por `ts`.
    `ticker` = ticker_corto. NO cacheado: el tape va live con el poll del frontend."""
    inicio_hoy = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    lim = max(1, min(limite, 1000))
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT ts, price, size, side, money FROM mercado.cedears_time_sales "
            "WHERE ticker_corto = %s AND ts >= %s ORDER BY ts DESC LIMIT %s",
            (ticker.upper(), inicio_hoy, lim),
        )
        rows = cur.fetchall()
    out: list[dict] = []
    for d in rows:
        ts = d["ts"]
        out.append({
            "timestamp": (ts.astimezone(UTC).replace(tzinfo=None).isoformat()
                          if isinstance(ts, datetime) else ts),
            "price":     float(d["price"]) if d["price"] is not None else None,
            "size":      float(d["size"])  if d["size"]  is not None else None,
            "side":      d["side"],
            "money":     float(d["money"]) if d["money"] is not None else None,
        })
    return out


def get_cedears_intraday(*, ticker: str) -> list[dict]:
    """Serie intradía por minuto (OHLC + vol) del CEDEAR, agregada desde el Time & Sales
    de hoy (mercado.cedears_time_sales, SQL-native). open/close = primer/último precio del
    minuto por `ts` (array_agg ordenado, `id` como desempate)."""
    inicio_hoy = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
                to_char(date_trunc('minute', ts AT TIME ZONE 'UTC'),
                        'YYYY-MM-DD"T"HH24:MI:00"Z"')           AS t,
                (array_agg(price ORDER BY ts, id))[1]           AS o,
                max(price)                                      AS h,
                min(price)                                      AS l,
                (array_agg(price ORDER BY ts DESC, id DESC))[1] AS c,
                COALESCE(sum(size), 0)                          AS vol
            FROM mercado.cedears_time_sales
            WHERE ticker_corto = %s AND ts >= %s
            GROUP BY date_trunc('minute', ts AT TIME ZONE 'UTC')
            ORDER BY date_trunc('minute', ts AT TIME ZONE 'UTC')
            """,
            (ticker.upper(), inicio_hoy),
        )
        rows = cur.fetchall()

    def _f(x):
        return float(x) if x is not None else None

    return [
        {"t": d["t"], "o": _f(d["o"]), "h": _f(d["h"]),
         "l": _f(d["l"]), "c": _f(d["c"]), "vol": _f(d["vol"])}
        for d in rows
    ]
