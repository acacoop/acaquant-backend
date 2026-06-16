"""consolidado_cuentas.py — precalcula la valuación consolidada por cuenta.

La vista TOTALES > POR CUENTA necesita, por cada cuenta, valor + base 100
+ PnL acumulado (ARS y USD). Calcularlo en vivo recorre N cuentas llamando
`valuacion_mensual` → se pasa del timeout HTTP y la web devuelve 502.

Este job lo calcula offline (sin límite de tiempo) y lo persiste en
`Valuaciones.ConsolidadoCuentas` (Mongo) Y `portafolio.consolidado` (SQL, dual-write).
El endpoint `/api/valuaciones/consolidado` lee uno u otro según VALUACIONES_SQL —
instantáneo. Mismo patrón que `Trading.SnapshotsCierre`.

Corre como cron diario después del AuM final (jobs.aum, 23 UTC L-V).
  python -m jobs.consolidado_cuentas
"""
from __future__ import annotations

from datetime import UTC, datetime

from api.services.valuaciones import construir_consolidado
from core.job_runs import JobRunLogger
from core.mongo import get_mongo_client, reemplazar_coleccion_atomico

_SQL_COLS = ("id_cuenta", "cuenta", "ultimo_dia", "valor_ars", "valor_usd",
             "base100_ars", "base100_usd", "pnl_acum_ars", "pnl_acum_usd",
             "tem_ars", "tem_usd", "tea_ars", "tea_usd")


def _persistir_sql(filas: list[dict]) -> int:
    """Dual-write a `portafolio.consolidado` (cache SQL del path VALUACIONES_SQL).

    Self-crea la tabla (CREATE TABLE IF NOT EXISTS) → no hace falta aplicar el schema
    aparte. Swap por TRUNCATE+INSERT en una transacción; dedup por id_cuenta."""
    from core.postgres import get_pool

    vistos: set[str] = set()
    rows = []
    for f in filas:
        idc = str(f.get("id_cuenta"))
        if idc in vistos:
            continue
        vistos.add(idc)
        rows.append((idc, *(f.get(c) for c in _SQL_COLS[1:])))

    ph = ", ".join(["%s"] * len(_SQL_COLS))
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS portafolio.consolidado (
                id_cuenta text PRIMARY KEY, cuenta text, ultimo_dia text,
                valor_ars numeric, valor_usd numeric,
                base100_ars numeric, base100_usd numeric,
                pnl_acum_ars numeric, pnl_acum_usd numeric,
                tem_ars numeric, tem_usd numeric, tea_ars numeric, tea_usd numeric,
                computed_at timestamptz DEFAULT now())""")
        cur.execute("TRUNCATE portafolio.consolidado")
        if rows:
            cur.executemany(
                f"INSERT INTO portafolio.consolidado ({', '.join(_SQL_COLS)}) VALUES ({ph})", rows)
        conn.commit()
    return len(rows)


def main() -> None:
    with JobRunLogger("consolidado_cuentas") as jr:
        jr.log("calculando fila por cuenta…")
        filas = construir_consolidado()
        ahora = datetime.now(UTC)
        for f in filas:
            f["computed_at"] = ahora

        # Swap atómico (sin ventana de vacío): /consolidado lee find({}).
        db_v = get_mongo_client()["Valuaciones"]
        n = reemplazar_coleccion_atomico(db_v, "ConsolidadoCuentas", filas)
        if n > 0:
            jr.set_stat("cuentas", n)
            jr.log(f"✅ {n} cuentas persistidas en Valuaciones.ConsolidadoCuentas")
        else:
            jr.error("construir_consolidado devolvió 0 filas — colección NO tocada")

        # Dual-write SQL — en try aparte: si PG falla, el cache Mongo (path vivo) NO se afecta.
        try:
            ns = _persistir_sql(filas)
            jr.log(f"✅ {ns} cuentas → portafolio.consolidado (SQL)")
        except Exception as e:
            jr.log(f"⚠️ dual-write SQL falló (Mongo OK, no bloqueante): {e}")


if __name__ == "__main__":
    main()
