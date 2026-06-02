"""Crea un índice que CUBRE la serie de /ops/aranceles → consulta sin FETCH.

Diagnóstico (scripts.diag_perf_aranceles, medido 2026-06-02): la serie ya usa
IXSCAN sobre `moneda_concertacion` PERO hace FETCH de ~178k (ARS) / ~302k (USD)
documentos porque el índice no tiene `arancel` (para sumar) ni `tipo_operacion`
(para el filtro NO-Cierre) → ~1–1,8s de primer load.

Este índice incluye TODOS los campos que toca la serie:
    {moneda, segmento, concertacion, tipo_operacion, arancel}
→ Mongo resuelve match + group + sum dentro del índice (covered query), sin abrir
documentos. Cubre tanto el caso sin segmento como el filtrado por segmento.

Tras crearlo, RE-CORRÉ `python -m scripts.diag_perf_aranceles` para verificar
que `docsExaminados` cae a ~0 y el wall-clock baja. Si el planner lo elige, se
agrega a operaciones_informes.ensure_indexes para que sea permanente.

Uso (desde la raíz del repo, en el Droplet):
    python -m scripts.crear_indice_serie_aranceles
"""
from __future__ import annotations

from core.mongo import get_mongo_client

_NOMBRE = "serie_aranceles_cov"
_SPEC = [
    ("moneda", 1), ("segmento", 1), ("concertacion", 1),
    ("tipo_operacion", 1), ("arancel", 1),
]


def main() -> None:
    coll = get_mongo_client()["CashFlow"]["Operaciones"]
    existentes = {ix["name"] for ix in coll.list_indexes()}
    if _NOMBRE in existentes:
        print(f"El índice {_NOMBRE} ya existe. Nada que hacer.")
        return
    print(f"Creando índice {_NOMBRE} = {_SPEC} … (puede tardar en ~482k docs)")
    coll.create_index(_SPEC, name=_NOMBRE)
    print("✅ Creado. Ahora corré: python -m scripts.diag_perf_aranceles")
    print("   Esperado: plan=IXSCAN (serie_aranceles_cov), docsExaminados ~0.")


if __name__ == "__main__":
    main()
