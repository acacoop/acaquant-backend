"""fix_assets_faltantes.py — crea en Valuaciones.Assets las unidades de
AuM que hoy faltan.

El job jobs/aum.py recién ahora sincroniza Valuaciones.Assets (antes solo
tocaba TitulosAPI.AssetsAPI). Este script aplica esa misma sincronización
de una, sin esperar al cron de las 23 UTC: toma todas las unidades que
aparecen en Valuaciones.AuM y que no existen en Valuaciones.Assets, y las
crea con los 7 campos UPPERCASE vacíos (+ CAFCI). Quedan visibles en
Manager → Assets para categorizar.

Reusa `_sincronizar_assets_valuaciones` de jobs/aum.py — misma lógica que
el cron, sin divergencia.

Dry-run por default. Pasa --apply para escribir.

Corre:  python -m scripts.fix_assets_faltantes [--apply]
"""
from __future__ import annotations

import sys

from core.mongo import get_mongo_client
from jobs.aum import _sincronizar_assets_valuaciones


def main() -> None:
    apply = "--apply" in sys.argv
    db_val = get_mongo_client()["Valuaciones"]

    unidades_aum = set(db_val["AuM"].distinct("unidad"))
    unidades_assets = set(db_val["Assets"].distinct("unidad"))
    faltantes = sorted(unidades_aum - unidades_assets)

    print(f"Unidades en Valuaciones.AuM:    {len(unidades_aum)}")
    print(f"Unidades en Valuaciones.Assets: {len(unidades_assets)}")
    print(f"Faltan en Valuaciones.Assets:   {len(faltantes)}")
    print("=" * 72)
    for u in faltantes:
        print(f"  - {u}")

    if not faltantes:
        print("\nNada que crear — todas las unidades de AuM ya estan en Assets.")
        return

    if not apply:
        print(f"\nDRY-RUN. Volve a correr con --apply para crear las {len(faltantes)} "
              f"unidades en Valuaciones.Assets.")
        return

    _sincronizar_assets_valuaciones(db_val["Assets"], faltantes)
    print(f"\nOK: {len(faltantes)} unidades creadas en Valuaciones.Assets "
          f"(campos UPPERCASE vacios — completar desde Manager -> Assets).")


if __name__ == "__main__":
    main()
