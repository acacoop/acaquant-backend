"""Diag READ-ONLY: verifica que el `cupo` de las Comitentes está intacto.

Cuenta cuántas cuentas tienen cupo cargado y muestra ejemplos. NO escribe nada.

Uso:
    python -m scripts.diag_check_cupo
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read


def main() -> None:
    col = get_mongo_client_read()["Clientes"]["Comitentes"]
    total = col.count_documents({})
    con_cupo = col.count_documents({"cupo.transaccional_ars": {"$gt": 0}})
    con_usado = col.count_documents({"cupo.usado_ars": {"$gt": 0}})
    print(f"Comitentes totales: {total}")
    print(f"  con cupo.transaccional_ars > 0: {con_cupo}")
    print(f"  con cupo.usado_ars > 0:         {con_usado}")

    print("\nEjemplos (cuentas con cupo cargado):")
    for d in col.find({"cupo.transaccional_ars": {"$gt": 0}},
                      {"_id": 0, "id_cuenta": 1, "denominacion": 1, "nivel_3": 1, "cupo": 1}).limit(8):
        c = d.get("cupo") or {}
        print(f"  [{d.get('id_cuenta')}] {str(d.get('denominacion'))[:30]:<30} "
              f"cupo_trans={c.get('transaccional_ars')} usado={c.get('usado_ars')} "
              f"cargado_en={c.get('cargado_en')} | nivel_3={d.get('nivel_3')}")

    print("\n(read-only: no se escribió nada)")


if __name__ == "__main__":
    main()
