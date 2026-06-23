"""api/services/opciones_sql.py — chain LIVE de opciones leyendo Postgres.

Espejo SQL-native de `opciones.get_opciones` (el grid de /derivados). Lee
`mercado.options_snapshot` (dual-write del motor engines/options.py bajo SNAPSHOT_SQL).
Los greeks (delta/gamma/iv/theta/vega) los calcula el motor vía quant/black_scholes — acá
se DEVUELVEN tal cual (no se recalculan). Solo strikes vigentes (el motor purga las series
viejas en Mongo y SQL → la tabla no acumula vencimientos pasados, a diferencia de Data).

Lo no migrado (metadata, Data/DataHistorica de los charts) sigue en el módulo Mongo.
Dual-run flag `OPCIONES_SQL` (+ `?_engine`). El shape de salida == el path Mongo.
"""
from __future__ import annotations

from datetime import UTC, datetime

from psycopg.rows import dict_row

from api.cache import cached
from core.postgres import get_pool

# Proyección del chain (mismo shape que el path Mongo: symbol→instrumento + estos campos).
_OPC_FIELDS = ("bid", "offer", "last", "open", "high", "low", "ev", "spot", "strike",
               "tipo", "vence", "closing_price", "delta", "gamma", "iv", "theta", "vega",
               "updated_at")


def _project(d: dict) -> dict:
    out = {"instrumento": d.get("symbol")}
    for f in _OPC_FIELDS:
        if f in d:
            out[f] = d[f]
    return out


@cached(ttl=60)
def get_opciones(instrumento: str | None = None, tipo: str | None = None) -> list:
    """Chain live de opciones desde SQL. Solo con tick HOY (updated_at >= inicio de hoy,
    naive == criterio del motor `datetime.now()`). Filtros opcionales por symbol/tipo."""
    inicio_hoy = datetime.now(UTC).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    where = ["updated_at >= %s"]
    params: list = [inicio_hoy]
    if instrumento:
        where.append("symbol ILIKE %s")
        params.append(f"%{instrumento}%")
    if tipo:
        where.append("tipo = %s")
        params.append(tipo.upper())
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT data FROM mercado.options_snapshot WHERE {' AND '.join(where)}",
                    tuple(params))
        return [_project(r["data"]) for r in cur.fetchall()]
