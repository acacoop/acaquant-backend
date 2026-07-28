"""consolidado_cuentas.py — precalcula la valuación consolidada por cuenta.

La vista TOTALES > POR CUENTA necesita, por cada cuenta, valor + base 100
+ PnL acumulado (ARS y USD). Calcularlo en vivo recorre N cuentas llamando
`valuacion_mensual` → se pasa del timeout HTTP y la web devuelve 502.

Este job lo calcula offline (sin límite de tiempo) y lo persiste SQL-native en
`valuaciones.consolidado`. El endpoint `/api/valuaciones/consolidado` lo lee
(VALUACIONES_SQL=1) — instantáneo.

SQL es la única fuente (Mongo decomisado 2026-06-29).

Corre como cron diario después del AuM final.
  python -m jobs.consolidado_cuentas
"""
from __future__ import annotations

from datetime import UTC, datetime

from api.services.valuaciones import construir_consolidado
from core.job_runs import JobRunLogger

_SQL_COLS = ("id_cuenta", "cuenta", "ultimo_dia", "valor_ars", "valor_usd",
             "base100_ars", "base100_usd", "pnl_acum_ars", "pnl_acum_usd",
             "tem_ars", "tem_usd", "tea_ars", "tea_usd")


def _persistir_sql(filas: list[dict]) -> int:
    """Persiste `valuaciones.consolidado` (cache SQL del path VALUACIONES_SQL — fuente única).

    Self-crea la tabla (CREATE TABLE IF NOT EXISTS) → no hace falta aplicar el schema
    aparte. Swap por TRUNCATE+INSERT en una transacción; dedup por id_cuenta."""
    from core.postgres import get_job_pool

    vistos: set[str] = set()
    rows = []
    for f in filas:
        idc = str(f.get("id_cuenta"))
        if idc in vistos:
            continue
        vistos.add(idc)
        rows.append((idc, *(f.get(c) for c in _SQL_COLS[1:])))

    ph = ", ".join(["%s"] * len(_SQL_COLS))
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS valuaciones")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS valuaciones.consolidado (
                id_cuenta text PRIMARY KEY, cuenta text, ultimo_dia text,
                valor_ars numeric, valor_usd numeric,
                base100_ars numeric, base100_usd numeric,
                pnl_acum_ars numeric, pnl_acum_usd numeric,
                tem_ars numeric, tem_usd numeric, tea_ars numeric, tea_usd numeric,
                computed_at timestamptz DEFAULT now())""")
        cur.execute("TRUNCATE valuaciones.consolidado")
        if rows:
            cur.executemany(
                f"INSERT INTO valuaciones.consolidado ({', '.join(_SQL_COLS)}) VALUES ({ph})", rows)
        conn.commit()
    return len(rows)


def main() -> None:
    with JobRunLogger("consolidado_cuentas") as jr:
        jr.log("calculando fila por cuenta…")
        filas = construir_consolidado()
        ahora = datetime.now(UTC)
        for f in filas:
            f["computed_at"] = ahora

        if not filas:
            jr.error("construir_consolidado devolvió 0 filas — cache NO tocada")
            return

        # SQL-native: swap atómico (TRUNCATE+INSERT en una transacción) → sin ventana de vacío.
        ns = _persistir_sql(filas)
        jr.set_stat("cuentas", ns)
        jr.log(f"✅ {ns} cuentas → valuaciones.consolidado (SQL)")


if __name__ == "__main__":
    main()
