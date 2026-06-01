"""diag_productores.py — lista el contenido de CashFlow.Productores.

Read-only. El filtro "Solo productores" de la vista Negocio → Movimientos
(api/services/_cuentas_filter.py::_cuentas_productores) toma las cuentas que
están en esta colección. La colección se mantiene A MANO — NO la escribe
ningún código del repo (ni job ni endpoint). Este diag muestra QUIÉNES son:
por cada cuenta, el `accionista` asociado + el nombre del titular
(`denominacion`), operador y `nivel_1` cruzados desde Clientes.Comitentes.

Uso (desde la raíz del repo en el Droplet):
    venv/bin/python -m scripts.diag_productores
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read


def main() -> None:
    client = get_mongo_client_read()
    productores = list(client["CashFlow"]["Productores"].find({}, {"_id": 0}))

    print("=" * 110)
    print(f"CashFlow.Productores — {len(productores)} cuenta(s) en la lista")
    print("Esta colección se mantiene A MANO; es la que usa el filtro 'Solo productores'.")
    print("=" * 110)

    if not productores:
        print("VACÍA — no hay ninguna cuenta cargada como productor.")
        return

    # Cruce con Comitentes por `cuenta` para traer nombre / operador / segmento.
    comit = client["Clientes"]["Comitentes"]
    cuentas = [str(p.get("cuenta")) for p in productores if p.get("cuenta")]
    info: dict[str, dict] = {}
    for d in comit.find(
        {"cuenta": {"$in": cuentas}},
        {"_id": 0, "cuenta": 1, "denominacion": 1, "operador_nombre": 1, "nivel_1": 1},
    ):
        info[str(d.get("cuenta"))] = d

    print(
        f"\n{'CUENTA':<12} {'TITULAR (denominación)':<40} "
        f"{'ACCIONISTA':<20} {'OPERADOR':<18} NIVEL_1"
    )
    print("-" * 110)
    for p in sorted(productores, key=lambda x: str(x.get("cuenta") or "")):
        cuenta = str(p.get("cuenta") or "—")
        accionista = str(p.get("accionista") or "—")
        c = info.get(cuenta, {})
        titular = str(c.get("denominacion") or "(no está en Comitentes)")
        operador = str(c.get("operador_nombre") or "—")
        nivel1 = str(c.get("nivel_1") or "—")
        print(
            f"{cuenta:<12} {titular[:39]:<40} {accionista[:19]:<20} "
            f"{operador[:17]:<18} {nivel1}"
        )

    sin_match = sum(
        1 for p in productores if str(p.get("cuenta") or "") not in info
    )
    print("-" * 110)
    print(
        f"Total: {len(productores)} · con datos en Comitentes: "
        f"{len(productores) - sin_match} · sin match: {sin_match}"
    )
    campos = sorted({k for p in productores for k in p.keys()})
    print(f"Campos presentes en los docs de Productores: {', '.join(campos)}")


if __name__ == "__main__":
    main()
