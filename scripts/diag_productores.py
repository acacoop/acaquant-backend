"""diag_productores.py — lista los PRODUCTORES (Clientes.Comitentes.nivel_1).

Read-only. El filtro "Solo productores" de la vista Negocio → Movimientos
(api/services/_cuentas_filter.py::_ids_cuenta_productores) toma los comitentes
cuya segmentación `nivel_1` es 'PRODUCTORES' y los cruza con los movimientos /
AuM por `id_cuenta` (NO por el string `cuenta` '[534] EGUREN, NE', que solo vive
en los movs). Este diag muestra QUIÉNES son: id_cuenta, titular (`denominacion`),
operador y nivel_2.

(Antes la fuente era la colección CashFlow.Productores, mantenida a mano; se
migró a la segmentación nivel_1 == PRODUCTORES de Comitentes.)

Uso (desde la raíz del repo en el Droplet):
    venv/bin/python -m scripts.diag_productores
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read


def main() -> None:
    client = get_mongo_client_read()
    comit = client["Clientes"]["Comitentes"]
    prods = list(
        comit.find(
            {"nivel_1": "PRODUCTORES"},
            {"_id": 0, "id_cuenta": 1, "denominacion": 1,
             "operador_nombre": 1, "nivel_2": 1},
        )
    )

    print("=" * 100)
    print(f"PRODUCTORES (Comitentes.nivel_1 == 'PRODUCTORES') — {len(prods)} cuenta(s)")
    print("Fuente del filtro 'Solo productores'. La relación con los movimientos es por id_cuenta.")
    print("=" * 100)

    if not prods:
        print("NINGUNA — no hay comitentes con nivel_1 = 'PRODUCTORES'.")
        print("(Chequeá el casing: los niveles se guardan en MAYÚSCULAS.)")
        valores = comit.distinct("nivel_1")
        print(f"Valores de nivel_1 presentes: {sorted(str(v) for v in valores if v)}")
        return

    print(f"\n{'ID_CUENTA':<12} {'TITULAR (denominación)':<45} {'OPERADOR':<20} NIVEL_2")
    print("-" * 100)
    sin_id = 0
    for p in sorted(prods, key=lambda x: str(x.get("denominacion") or "")):
        idc = p.get("id_cuenta")
        if idc is None:
            sin_id += 1
        idc_s = str(idc) if idc is not None else "—"
        titular = str(p.get("denominacion") or "—")
        operador = str(p.get("operador_nombre") or "—")
        nivel2 = str(p.get("nivel_2") or "—")
        print(f"{idc_s:<12} {titular[:44]:<45} {operador[:19]:<20} {nivel2}")

    print("-" * 100)
    print(f"Total: {len(prods)} · sin `id_cuenta` (no entran al filtro): {sin_id}")


if __name__ == "__main__":
    main()
