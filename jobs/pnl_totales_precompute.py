"""pnl_totales_precompute.py — precalcula el PnL de TODAS las cuentas.

La vista TOTALES (PnL por título, todas las cuentas) recorría ~883 cuentas
en vivo en cada request HTTP → se pasaba del timeout (502) y no escalaba
con la cantidad de usuarios.

Este job hace ese cálculo offline (sin límite de tiempo) y lo persiste SQL-native en
`valuaciones.pnl_totales_cache` — un documento por cuenta, con sus filas y el detalle
de boletos. El endpoint `/api/portfolio/pnl-todas` lo lee (PNL_TOTALES_SQL=1) — instantáneo.

Cutover 2026-06-26: dejó de escribir `Valuaciones.PnLTotalesCache` (Mongo). SQL es la
fuente; Mongo quedó huérfana y se dropea (scripts/drop_mongo_migradas).

Corre por cron cada 30 min en la rueda (:05 y :35), después de
`negocio_movimientos`.
  python -m jobs.pnl_totales_precompute
"""
from __future__ import annotations

from datetime import UTC, datetime

from api.services.pnl_sql import pnl_todas_cuentas_compute_sql
from core.job_runs import JobRunLogger
from core.postgres import use_job_pool


def _persistir_sql(cuentas: list[dict]) -> int:
    """Persiste `valuaciones.pnl_totales_cache` (cache SQL del path PNL_TOTALES_SQL — fuente única).

    Swap atómico (TRUNCATE+INSERT en una transacción, vía pg_mirror.replace_native) →
    sin ventana de vacío, igual que el swap Mongo. Dedup por id_cuenta (PK). `rows`/
    `totales` (dict/list) → jsonb automático; `computed_at` (datetime aware) → timestamptz."""
    from core.pg_mirror import replace_native

    vistos: set[str] = set()
    rows: list[dict] = []
    for d in cuentas:
        idc = str(d.get("id_cuenta"))
        if idc in vistos:
            continue
        vistos.add(idc)
        rows.append({
            "id_cuenta":   idc,
            "cuenta":      d.get("cuenta") or "",
            "rows":        d.get("rows", []),
            "totales":     d.get("totales", {}) or {},
            "computed_at": d.get("computed_at"),
        })
    return replace_native("valuaciones.pnl_totales_cache", rows)


def main() -> None:
    # use_job_pool: el cómputo + la persistencia corren en el carril de jobs
    # (aislado, timeout 30s) en vez del carril web (8s) — evita el PoolTimeout en
    # rueda. pnl_sql/portfolio_sql/pg_mirror llaman get_pool() por debajo y, dentro
    # de este bloque, get_pool() devuelve el carril de jobs.
    with JobRunLogger("pnl_totales_precompute") as jr, use_job_pool():
        jr.log("calculando PnL de todas las cuentas (SQL)…")
        cuentas = pnl_todas_cuentas_compute_sql()
        ahora = datetime.now(UTC)
        for d in cuentas:
            d["computed_at"] = ahora

        if not cuentas:
            jr.error("pnl_todas_cuentas_compute devolvió 0 cuentas — cache NO tocada")
            return

        # SQL-native: swap atómico (replace_native = TRUNCATE+INSERT transaccional) → sin vacío.
        ns = _persistir_sql(cuentas)
        n_filas = sum(len(d.get("rows", [])) for d in cuentas)
        jr.set_stat("cuentas", ns)
        jr.set_stat("filas", n_filas)
        jr.log(f"✅ {ns} cuentas ({n_filas} filas) → valuaciones.pnl_totales_cache (SQL)")


if __name__ == "__main__":
    main()
