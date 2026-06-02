"""Borra el índice serie_aranceles_cov — NO sirvió (medido).

El índice {moneda,segmento,concertacion,tipo_operacion,arancel} se creó para
cubrir la serie de /ops/aranceles, pero el explain mostró que el $group de
aggregation sigue haciendo FETCH por documento (docsExaminados == keysExaminadas)
→ no cubre, no mejora el wall-clock. Se elimina para no dejar un índice grande
inútil ocupando RAM/disco.

Uso (desde la raíz del repo, en el Droplet):
    python -m scripts.drop_indice_serie_aranceles
"""
from __future__ import annotations

from core.mongo import get_mongo_client

_NOMBRE = "serie_aranceles_cov"


def main() -> None:
    coll = get_mongo_client()["CashFlow"]["Operaciones"]
    if _NOMBRE not in {ix["name"] for ix in coll.list_indexes()}:
        print(f"El índice {_NOMBRE} no existe. Nada que borrar.")
        return
    coll.drop_index(_NOMBRE)
    print(f"✅ Índice {_NOMBRE} borrado.")


if __name__ == "__main__":
    main()
