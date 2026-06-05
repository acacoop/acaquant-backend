"""consolidado_cuentas.py — precalcula la valuación consolidada por cuenta.

La vista TOTALES > POR CUENTA necesita, por cada cuenta, valor + base 100
+ PnL acumulado (ARS y USD). Calcularlo en vivo recorre N cuentas llamando
`valuacion_mensual` → se pasa del timeout HTTP y la web devuelve 502.

Este job lo calcula offline (sin límite de tiempo) y lo persiste en
`Valuaciones.ConsolidadoCuentas`. El endpoint `/api/valuaciones/consolidado`
sólo lee esa colección — instantáneo. Mismo patrón que `Trading.SnapshotsCierre`.

Corre como cron diario después del AuM final (jobs.aum, 23 UTC L-V).
  python -m jobs.consolidado_cuentas
"""
from __future__ import annotations

from datetime import UTC, datetime

from api.services.valuaciones import construir_consolidado
from core.job_runs import JobRunLogger
from core.mongo import get_mongo_client, reemplazar_coleccion_atomico


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


if __name__ == "__main__":
    main()
