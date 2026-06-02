"""Diag READ-ONLY para diseñar la unificación de las SOLICITUDES FCI bilateral
(hoy solo en CashFlow.NegocioMovimientos) dentro de CashFlow.Operaciones.

Contesta, con dato real (REGLA #2), lo que hay que clavar antes de escribir el job:
  1. Cuántas solicitudes FCI hay y cuántos docs por comprobante (¿n_lineas → hay
     que colapsar a 1 antes de upsertear por comprobante?).
  2. Cobertura del monto: importe vs cantidad por categoría (de dónde sale `bruto`).
  3. Qué `estado` / `op` traen.
  4. DOBLE CONTEO: ¿Operaciones YA trae FCI bilateral / la liquidación como boleto?
     (si sí, meter la solicitud contaría dos veces la misma operación).
  5. El catálogo TiposOperacion para FCI Bilateral (naming de referencia).

NO escribe nada. Cliente de solo lectura.

Uso (desde la raíz del repo, en el Droplet):
    python -m scripts.diag_fci_bilateral
"""
from __future__ import annotations

from collections import Counter

from core.mongo import get_mongo_client_read

_CATS = ["solicitud_suscripcion_fci", "solicitud_rescate_fci"]


def _grupo(coll, match: dict, campo: str) -> dict:
    return {
        d["_id"]: d["n"]
        for d in coll.aggregate([
            {"$match": match},
            {"$group": {"_id": f"${campo}", "n": {"$sum": 1}}},
            {"$sort": {"n": -1}},
        ])
    }


def main() -> None:
    db = get_mongo_client_read()["CashFlow"]
    mov = db["NegocioMovimientos"]
    ops = db["Operaciones"]
    tipos = db["TiposOperacion"]

    print("══ 1) Solicitudes FCI en NegocioMovimientos ══")
    n = mov.count_documents({"categoria": {"$in": _CATS}})
    print(f"Total docs (categoria in {_CATS}): {n}")
    por_comp = list(mov.aggregate([
        {"$match": {"categoria": {"$in": _CATS}}},
        {"$group": {"_id": "$comprobante", "n": {"$sum": 1}}},
    ]))
    dist = Counter(d["n"] for d in por_comp)
    print(f"  comprobantes distintos: {len(por_comp)}")
    print(f"  distribución (docs por comprobante → cuántos comprobantes): {dict(sorted(dist.items()))}")

    print("\n══ 2) Cobertura del monto por categoría ══")
    for cat in _CATS:
        tot = mov.count_documents({"categoria": cat})
        con_imp = mov.count_documents({"categoria": cat, "importe": {"$ne": None}})
        con_cant = mov.count_documents({"categoria": cat, "cantidad": {"$ne": None}})
        print(f"  {cat}: total={tot} | con importe≠null={con_imp} | con cantidad≠null={con_cant}")

    print("\n══ 3) Estados y 'op' presentes ══")
    print("  estados:", _grupo(mov, {"categoria": {"$in": _CATS}}, "estado"))
    print("  op:     ", _grupo(mov, {"categoria": {"$in": _CATS}}, "op"))

    print("\n══ 4) ¿Operaciones YA trae FCI bilateral / liquidación? (doble conteo) ══")
    f_merc = ops.count_documents({"mercado": "FCI Bilateral"})
    f_tipo = ops.count_documents({"tipo_operacion": {"$regex": "FCI", "$options": "i"}})
    f_liq = ops.count_documents({"tipo_operacion": {"$regex": "Liquidaci", "$options": "i"}})
    print(f"  mercado='FCI Bilateral': {f_merc}")
    print(f"  tipo_operacion ~ 'FCI': {f_tipo}")
    print(f"  tipo_operacion ~ 'Liquidaci': {f_liq}")
    if f_tipo:
        print("  ejemplos (tipo_operacion ~ FCI):")
        for d in ops.find({"tipo_operacion": {"$regex": "FCI", "$options": "i"}},
                          {"_id": 0, "boleto": 1, "cuenta": 1, "concertacion": 1,
                           "tipo_operacion": 1, "operacion": 1, "instrumento": 1, "bruto": 1}).limit(5):
            print("   ", d)

    print("\n══ 5) Catálogo TiposOperacion (FCI / Bilateral) ══")
    for t in tipos.find({"$or": [{"mercado": {"$regex": "FCI|Bilateral", "$options": "i"}},
                                 {"tipo_operacion": {"$regex": "FCI", "$options": "i"}}]},
                        {"_id": 0, "tipo_operacion": 1, "mercado": 1, "operacion": 1}):
        print("   ", t)

    print("\n══ Ejemplos de solicitudes (NegocioMovimientos) ══")
    for d in mov.find({"categoria": {"$in": _CATS}},
                      {"_id": 0, "comprobante": 1, "categoria": 1, "op": 1, "estado": 1,
                       "importe": 1, "cantidad": 1, "moneda": 1, "id_cuenta": 1,
                       "ticker": 1, "informacion": 1, "n_lineas": 1}).limit(4):
        print("   ", d)

    print("\n(read-only: no se escribió nada)")


if __name__ == "__main__":
    main()
