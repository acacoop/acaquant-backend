"""scripts/diag_dolarizar_cobertura.py — ¿se pueden dolarizar TODOS los
movimientos? Mide la cobertura real del tipo de cambio (`mep`) en las dos
colecciones de negocio, antes de escribir cualquier conversión a USD.

POR QUÉ: dolarizar un boleto ARS sin su `mep` lo manda a 0 → subcontaría el
volumen en silencio. El usuario afirma que "todos deberían tener el mep" — pero
"deberían" no está medido. Este diag confirma, read-only, qué proporción de docs
tiene `mep` usable (>0), partido por moneda, en:

  1. CashFlow.NegocioMovimientos  → ya tiene endpoints que convierten per-boleto.
  2. CashFlow.Operaciones         → la pestaña OPERACIONES que el usuario ve.
                                     ¿Existe el campo `mep` acá? ¿O hay que
                                     enriquecerlo antes de poder dolarizar?

Salida: por colección y por moneda → total docs, con `mep`>0, sin `mep`, y el %.
Las filas ARS sin mep son las que romperían la conversión (volumen perdido).

Read-only. Correr en el Droplet:
    python -m scripts.diag_dolarizar_cobertura
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read


def _reporte_coleccion(coll, nombre: str) -> None:
    print("=" * 70)
    print(f"{nombre}  (total docs: {coll.estimated_document_count():,})")
    print("=" * 70)

    # ¿Existe siquiera el campo mep en la colección?
    con_campo = coll.count_documents({"mep": {"$exists": True}})
    if con_campo == 0:
        print("  ⚠ NINGÚN doc tiene el campo `mep`. Esta colección NO se puede")
        print("    dolarizar per-doc sin enriquecerla antes (faltaría el TC).")
        # Igual mostramos el desglose por moneda para ver el universo.
    print(f"  docs con campo `mep` presente: {con_campo:,}\n")

    pipeline = [
        {"$group": {
            "_id": {"$ifNull": ["$moneda", "(sin moneda)"]},
            "total":     {"$sum": 1},
            "mep_ok":    {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$mep", 0]}, 0]}, 1, 0]}},
        }},
        {"$sort": {"total": -1}},
    ]
    print(f"  {'moneda':<14}{'total':>10}{'mep>0':>10}{'sin mep':>10}{'% ok':>8}")
    print("  " + "-" * 50)
    for r in coll.aggregate(pipeline):
        moneda = r["_id"]
        total = r["total"]
        ok = r["mep_ok"]
        sin = total - ok
        pct = (ok / total * 100) if total else 0
        flag = "  <-- ARS sin mep = volumen perdido al dolarizar" if (
            str(moneda).upper() == "ARS" and sin > 0
        ) else ""
        print(f"  {moneda!s:<14}{total:>10,}{ok:>10,}{sin:>10,}{pct:>7.1f}%{flag}")
    print()


def main() -> None:
    cli = get_mongo_client_read()
    cf = cli["CashFlow"]

    _reporte_coleccion(cf["NegocioMovimientos"], "CashFlow.NegocioMovimientos")
    _reporte_coleccion(cf["Operaciones"], "CashFlow.Operaciones")

    print("=" * 70)
    print("LECTURA")
    print("=" * 70)
    print("- NegocioMovimientos: si ARS+USD tienen ~100% mep>0 → la conversión")
    print("  per-boleto que ya hace el backend es exacta. Si hay ARS sin mep,")
    print("  esos boletos hoy aportan 0 al total USD (hay que decidir qué hacer).")
    print("- Operaciones: si NO tiene campo `mep`, la pestaña OPERACIONES no se")
    print("  puede dolarizar per-doc sin un enriquecimiento previo (otro job).")


if __name__ == "__main__":
    main()
