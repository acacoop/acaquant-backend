"""pnl_totales_precompute.py — precalcula el PnL de TODAS las cuentas.

La vista TOTALES (PnL por título, todas las cuentas) recorría ~883 cuentas
en vivo en cada request HTTP → se pasaba del timeout (502) y no escalaba
con la cantidad de usuarios.

Este job hace ese cálculo offline (sin límite de tiempo) y lo persiste en
`Valuaciones.PnLTotalesCache` — un documento por cuenta, con sus filas y
el detalle de boletos. El endpoint `/api/portfolio/pnl-todas` solo lee esa
colección. Mismo patrón que `Trading.SnapshotsCierre`.

Corre por cron cada 30 min en la rueda (:05 y :35), después de
`negocio_movimientos`.
  python -m jobs.pnl_totales_precompute
"""
from __future__ import annotations

from datetime import UTC, datetime

from api.services.pnl import pnl_todas_cuentas_compute
from core.mongo import get_mongo_client, reemplazar_coleccion_atomico


def main() -> None:
    print("pnl_totales_precompute — calculando PnL de todas las cuentas…",
          flush=True)
    cuentas = pnl_todas_cuentas_compute()
    ahora = datetime.now(UTC)
    for d in cuentas:
        d["computed_at"] = ahora

    # Swap atómico (sin ventana de vacío): /pnl-todas lee find({}) → si borráramos
    # y reinsertáramos, un request en el medio vería cero. Ver core.mongo.
    db_v = get_mongo_client()["Valuaciones"]
    n = reemplazar_coleccion_atomico(db_v, "PnLTotalesCache", cuentas)
    if n > 0:
        n_filas = sum(len(d.get("rows", [])) for d in cuentas)
        print(f"✅ {n} cuentas ({n_filas} filas) persistidas en "
              f"Valuaciones.PnLTotalesCache ({ahora.isoformat()})")
    else:
        print("⚠ pnl_todas_cuentas_compute devolvió 0 cuentas — "
              "la colección NO se tocó.")


if __name__ == "__main__":
    main()
