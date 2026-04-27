"""inspect_a3500.py — qué A3500 está usando el motor de curvas.

Cuando los bonos dolar-linked salen con TEA negativa o paridad > 100%
suele ser porque Trading.DOLAR (BCRA fixing diario) está stale.
Este script muestra el último valor disponible y la edad del dato.

Uso:
    python -m scripts.inspect_a3500
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.mongo import get_mongo_client


def main() -> None:
    client = get_mongo_client()
    col = client["Trading"]["DOLAR"]

    doc = col.find_one(
        {"valor": {"$gt": 0}},
        {"_id": 0, "valor": 1, "fecha": 1, "updated_at": 1},
        sort=[("fecha", -1)],
    )
    if not doc:
        print("❌ Trading.DOLAR vacío — corré: python -m jobs.bcra --today")
        return

    valor = doc.get("valor")
    fecha = doc.get("fecha")
    updated_at = doc.get("updated_at")

    print(f"Último A3500: {valor:.4f}")
    print(f"Fecha       : {fecha}")
    if updated_at:
        print(f"Updated_at  : {updated_at}")

    # Edad del dato vs hoy
    hoy = datetime.now(UTC).date().isoformat()
    if fecha and isinstance(fecha, str) and fecha < hoy:
        try:
            f_doc = datetime.strptime(fecha, "%Y-%m-%d").date()
            dias_atras = (datetime.now(UTC).date() - f_doc).days
            print(f"\n⚠️  El dato es de hace {dias_atras} día(s). Hoy es {hoy}.")
            print("   Si la curva DL muestra TEA rara, este es el motivo.")
            print("   Solución: python -m jobs.bcra --today  →  systemctl restart motor_curvas.service")
        except ValueError:
            pass
    else:
        print(f"\n✓ Dato del día actual ({hoy}).")

    # Contar cuántos docs hay en total (sirve para ver si el job corrió)
    total = col.count_documents({})
    ult5 = list(col.find({}, {"_id": 0, "fecha": 1, "valor": 1}).sort("fecha", -1).limit(5))
    print(f"\nTotal docs en Trading.DOLAR: {total}")
    print("Últimos 5 fixings:")
    for d in ult5:
        print(f"  {d.get('fecha')}: {d.get('valor')}")


if __name__ == "__main__":
    main()
