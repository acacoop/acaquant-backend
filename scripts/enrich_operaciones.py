"""enrich_operaciones.py — denormaliza moneda + mercado + operacion sobre
CashFlow.Operaciones (join al catálogo CashFlow.TiposOperacion).

Correr DESPUÉS del backfill y cada vez que edites los `mercado` del catálogo.
Idempotente. Crea también los índices de la vista.

Uso (desde la raíz del repo en el Droplet):
    venv/bin/python -m scripts.enrich_operaciones
"""
from __future__ import annotations

from api.services import operaciones_informes as svc
from core.mongo import get_mongo_client


def main() -> None:
    db = get_mongo_client()["CashFlow"]
    print("Enriqueciendo CashFlow.Operaciones (moneda + mercado + operacion)…")
    res = svc.enriquecer(db)
    print(f"✅ {res['actualizados']} docs actualizados · "
          f"{res['sin_catalogo']} sin match en catálogo · "
          f"{res['tipos_catalogo']} tipos en catálogo")
    if res["sin_catalogo"]:
        print("⚠ Hay ops cuyo tipo_operacion no está en el catálogo → corré "
              "`python -m scripts.seed_tipos_operacion` para agregarlos, cargá el "
              "mercado y volvé a correr este enrich.")


if __name__ == "__main__":
    main()
