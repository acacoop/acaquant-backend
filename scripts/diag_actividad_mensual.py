"""diag_actividad_mensual.py — read-only: rango y volumen del histórico de
cuentas activas ANTES de backfillear.

Muestra, desde `CashFlow.NegocioMovimientos` (solo boletos operativos):
  - primer y último mes con actividad (= profundidad del histórico posible).
  - por mes: # cuentas activas distintas + # boletos operativos.

No escribe nada. Sirve para confirmar hasta dónde llega NM y sanity-check
contra lo que después persista `jobs/actividad_mensual.py`.

Uso:
    python -m scripts.diag_actividad_mensual
"""
from __future__ import annotations

from api.services.comercial import _CATS_OPERACIONES
from core.mongo import get_mongo_client_read


def main() -> None:
    mov = get_mongo_client_read()["CashFlow"]["NegocioMovimientos"]
    cats = list(_CATS_OPERACIONES)
    match = {"categoria": {"$in": cats}, "id_cuenta": {"$ne": None}}

    filas = list(mov.aggregate([
        {"$match": match},
        {"$group": {
            "_id": {"ym": {"$substrBytes": ["$fecha", 0, 7]}, "id": "$id_cuenta"},
            "n_ops": {"$sum": 1},
        }},
        {"$group": {
            "_id": "$_id.ym",
            "n_activas": {"$sum": 1},
            "n_ops": {"$sum": "$n_ops"},
        }},
        {"$sort": {"_id": 1}},
    ]))

    if not filas:
        print("⚠ Sin boletos operativos en NegocioMovimientos.")
        return

    print(f"Histórico disponible: {filas[0]['_id']} → {filas[-1]['_id']}  "
          f"({len(filas)} meses)\n")
    print(f"{'Mes':<9}{'Activas':>9}{'Boletos':>10}")
    print("-" * 28)
    for f in filas:
        print(f"{f['_id']:<9}{f['n_activas']:>9}{f['n_ops']:>10}")


if __name__ == "__main__":
    main()
