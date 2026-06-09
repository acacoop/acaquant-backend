"""scripts/diag_nivel5_agro.py — por qué el filtro nivel_5 del AGRO no filtra.

Read-only. Verifica la cadena: Comitentes.nivel_5 → id_cuentas → match contra
el campo `cuenta` de las operaciones AGRO (futuros SOJA/TRIGO/MAIZ).

Responde:
  A. ¿Existe y tiene valores el campo nivel_5 en Comitentes? distintos + #cuentas.
  B. Para el nivel_5 con más cuentas: ¿cuántos id_cuentas resuelve?
  C. ¿Qué FORMATO tiene el campo `cuenta` en las ops AGRO? (id numérico vs nombre)
     → si no son id_cuentas, el match {cuenta: {$in: id_cuentas}} nunca pega.
  D. ¿Cuántas ops AGRO matchean esas cuentas? (si 0 → el filtro vacía o no aplica)

Correr:  python -m scripts.diag_nivel5_agro
"""
from __future__ import annotations

from collections import Counter

from core.mongo import get_mongo_client_read


def main() -> None:
    cli = get_mongo_client_read()
    com = cli["Clientes"]["Comitentes"]
    ops = cli["CashFlow"]["Operaciones"]

    # A. nivel_5 en Comitentes.
    print("=" * 64)
    print("A. Comitentes.nivel_5")
    docs = list(com.find({}, {"_id": 0, "nivel_5": 1, "id_cuenta": 1}))
    tiene = sum(1 for d in docs if d.get("nivel_5"))
    print(f"   {len(docs)} comitentes · {tiene} con nivel_5 cargado")
    por_n5: Counter = Counter()
    for d in docs:
        por_n5[(d.get("nivel_5") or "(vacío)")] += 1
    print("   valores de nivel_5 (top 15):")
    for v, n in por_n5.most_common(15):
        print(f"     {v!s:<30} {n}")

    # B. el nivel_5 con más cuentas (excluyendo vacío).
    candidatos = [(v, n) for v, n in por_n5.most_common() if v != "(vacío)"]
    if not candidatos:
        print("\n⚠️  Ningún nivel_5 con valor → el filtro no tiene a qué apuntar.")
        return
    n5_val = candidatos[0][0]
    id_cuentas = {str(d["id_cuenta"]) for d in docs
                  if d.get("nivel_5") == n5_val and d.get("id_cuenta")}
    print("\n" + "=" * 64)
    print(f"B. nivel_5 de prueba: '{n5_val}' → {len(id_cuentas)} id_cuentas")
    print(f"   ej: {sorted(id_cuentas)[:10]}")

    # C. formato del campo `cuenta` en ops AGRO.
    print("\n" + "=" * 64)
    print("C. Campo `cuenta` en ops AGRO (commodity SOJA/TRIGO/MAIZ):")
    sample = list(ops.find(
        {"commodity": {"$in": ["SOJA", "TRIGO", "MAIZ"]}},
        {"_id": 0, "cuenta": 1, "denominacion": 1, "id_cuenta": 1}).limit(10))
    if not sample:
        print("   ⚠️  No hay ops AGRO (commodity SOJA/TRIGO/MAIZ). El agro está vacío.")
        return
    print(f"   {'cuenta':<16}{'id_cuenta':<14}denominacion")
    for s in sample:
        print(f"   {s.get('cuenta')!s:<16}{s.get('id_cuenta')!s:<14}{s.get('denominacion')}")
    campos_ops = set()
    for s in ops.find({"commodity": {"$in": ["SOJA", "TRIGO", "MAIZ"]}}, {"_id": 0}).limit(1):
        campos_ops = set(s.keys())
    print(f"   campos del doc AGRO: {sorted(campos_ops)}")

    # D. cuántas ops AGRO matchean esas cuentas (como hace el filtro).
    print("\n" + "=" * 64)
    n_match_cuenta = ops.count_documents(
        {"commodity": {"$in": ["SOJA", "TRIGO", "MAIZ"]}, "cuenta": {"$in": list(id_cuentas)}})
    n_total = ops.count_documents({"commodity": {"$in": ["SOJA", "TRIGO", "MAIZ"]}})
    print(f"D. Ops AGRO totales: {n_total}")
    print(f"   Ops AGRO con cuenta ∈ id_cuentas de '{n5_val}': {n_match_cuenta}")
    if n_match_cuenta == 0:
        print("   → ⚠️  El match por `cuenta`=id_cuenta da 0. Probablemente el campo")
        print("        de cuenta en AGRO NO es el id_cuenta (mirá el formato en C).")
    else:
        print("   → el match funciona; si en la vista no filtra, revisar API restart/cache.")

    print("\n✅ diag read-only completo — nada se escribió.")


if __name__ == "__main__":
    main()
