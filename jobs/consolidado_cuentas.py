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
from core.mongo import get_mongo_client


def main() -> None:
    print("consolidado_cuentas — calculando fila por cuenta…", flush=True)
    filas = construir_consolidado()
    ahora = datetime.now(UTC)
    for f in filas:
        f["computed_at"] = ahora

    col = get_mongo_client()["Valuaciones"]["ConsolidadoCuentas"]
    if filas:
        col.delete_many({})
        col.insert_many(filas)
        print(f"✅ {len(filas)} cuentas persistidas en "
              f"Valuaciones.ConsolidadoCuentas ({ahora.isoformat()})")
    else:
        print("⚠ construir_consolidado devolvió 0 filas — "
              "la colección NO se tocó.")


if __name__ == "__main__":
    main()
