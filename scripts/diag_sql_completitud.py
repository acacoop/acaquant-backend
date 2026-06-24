"""diag_sql_completitud.py — ¿el espejo SQL está COMPLETO vs Mongo? (gate para dropear).

Read-only. Por cada colección migrada, compara count Mongo vs count SQL + el rango de
fechas en SQL. Un espejo INCOMPLETO (SQL << Mongo, o sin historia) NO se puede dropear:
las vistas que necesitan historia (MTD/YTD, series) se rompen. Esta es la verificación
previa al drop de cada colección.

    python -m scripts.diag_sql_completitud
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read
from core.postgres import get_pool

# (db Mongo, coll Mongo, schema.tabla SQL, columna de fecha SQL | None)
_PARES = [
    ("Trading", "PreciosAcciones", "mercado.precios_acciones", "fecha"),
    ("Trading", "DayTradingStats", "mercado.day_trading_stats", "fecha"),
    ("Trading", "Cedears", "mercado.cedears", None),
    ("Trading", "MarketSnapshot", "mercado.market_snapshot", None),
    ("Trading", "SnapshotsCierre", "mercado.snapshots_cierre_hist", "fecha"),
    ("Trading", "CanjeCierre", "mercado.canje_cierre", "fecha"),
    ("CashFlow", "Movimientos", "operaciones.movimientos", None),
    ("CashFlow", "Acreencias", "operaciones.acreencias", "fecha_pago"),
    ("Opciones", "DataHistorica", "mercado.options_data_hist", None),
    ("Manager", "JobRuns", "manager.job_runs", "started_at"),
]


def main() -> int:
    mcli = get_mongo_client_read()
    print(f"{'COLECCIÓN':<34}{'MONGO':>10}{'SQL':>10}{'%':>7}  RANGO FECHAS SQL")
    print("─" * 92)
    with get_pool().connection() as conn:
        for db, coll, tabla, fcol in _PARES:
            try:
                n_mongo = mcli[db][coll].estimated_document_count()
            except Exception:
                n_mongo = -1
            with conn.cursor() as cur:
                try:
                    cur.execute(f"SELECT count(*) FROM {tabla}")
                    n_sql = cur.fetchone()[0]
                except Exception:
                    n_sql = -1
                rango = ""
                if fcol and n_sql > 0:
                    try:
                        cur.execute(f"SELECT min({fcol}), max({fcol}) FROM {tabla}")
                        lo, hi = cur.fetchone()
                        rango = f"{str(lo)[:10]} → {str(hi)[:10]}"
                    except Exception:
                        rango = "(sin col fecha)"
            pct = (100 * n_sql / n_mongo) if n_mongo > 0 else 0
            flag = ""
            if n_mongo > 0 and pct < 90:
                flag = "  ⚠ INCOMPLETO — backfillear antes de dropear"
            elif n_mongo > 0:
                flag = "  ✅"
            print(f"{db + '.' + coll:<34}{n_mongo:>10,}{n_sql:>10,}{pct:>6.0f}%  {rango}{flag}")
    print("\n⚠ INCOMPLETO = el SQL tiene < 90% de los docs de Mongo → NO dropear: las vistas que")
    print("  necesitan historia (MTD/YTD, series) se rompen. Backfill: jobs.sync_postgres --full|--days N.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
