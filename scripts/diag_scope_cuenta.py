"""scripts/diag_scope_cuenta.py — READ-ONLY: formato de `cuenta` / `id_cuenta`.

Para arreglar el scope de grupos (C5) SIN romper control de acceso, necesito
saber con CERTEZA (no asumir, REGLA #2) cómo es el campo `cuenta` en cada
colección y si tienen `id_cuenta` indexable:

  - api/services/_grupos_scope.py aplica `cuenta: {$regex: ^\\[(id)\\]}` a TODAS,
    pero comercial.py dice que en Operaciones `cuenta` == id numérico ("805").
    Si es así, ese regex NO matchea en Operaciones → el scope estaría roto ahí.

Este diag muestra muestras crudas + conteos. NO escribe nada.

Uso:
    python -m scripts.diag_scope_cuenta
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read


def _inspeccionar(col, nombre: str) -> None:
    total = col.estimated_document_count()
    print(f"\n── {nombre} ({total:,} docs aprox) ──")

    # ¿Existe id_cuenta? ¿en cuántos?
    con_idc = col.count_documents({"id_cuenta": {"$exists": True, "$nin": [None, ""]}})
    print(f"   con id_cuenta no vacío : {con_idc:,}  ({100*con_idc/total:.1f}%)" if total else "   (vacía)")

    # Muestra cruda de cuenta + id_cuenta.
    print("   muestra (cuenta | id_cuenta):")
    for d in col.find({}, {"_id": 0, "cuenta": 1, "id_cuenta": 1}).limit(8):
        print(f"      cuenta={d.get('cuenta')!r:<28} id_cuenta={d.get('id_cuenta')!r}")

    # ¿`cuenta` es bracketed "[..]" o id pelado?
    n_bracket = col.count_documents({"cuenta": {"$regex": r"^\["}})
    print(f"   cuenta que empieza con '[' : {n_bracket:,}  "
          f"({'bracketed' if n_bracket > total/2 else 'id pelado / mixto'})")


def main() -> int:
    cf = get_mongo_client_read()["CashFlow"]
    print("=" * 64)
    print("DIAG formato cuenta/id_cuenta — para arreglar el scope (C5)")
    print("=" * 64)
    _inspeccionar(cf["Operaciones"], "CashFlow.Operaciones")
    _inspeccionar(cf["NegocioMovimientos"], "CashFlow.NegocioMovimientos")
    print("\nDecisión que habilita esto:")
    print("  - Si Operaciones.cuenta es id pelado → el scope debe matchear")
    print("    {cuenta: {$in: scope}} (igualdad), NO el regex bracketed.")
    print("  - Si NegMov.cuenta es bracketed → ahí el regex ^[(id)] está bien,")
    print("    o mejor {id_cuenta: {$in: scope}} si está poblado.")
    print("\nread-only: no se escribió nada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
