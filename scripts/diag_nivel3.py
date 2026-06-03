"""scripts/diag_nivel3.py — READ-ONLY: por qué `nivel_3` está vacío en Operaciones.

`nivel_3` (y `segmento`) salen del enrich: join `Operaciones.cuenta` (id pelado)
→ `Clientes.Comitentes.id_cuenta` → `nivel_1`/`nivel_3`. Si está vacío hay DOS
causas posibles (y el fix difiere):
  - CAUSA 1 — el Comitente NO tiene nivel_3 cargado (segmentación pendiente).
    Un backfill NO lo arregla: la fuente está vacía. Hay que segmentar primero.
  - CAUSA 2 — el Comitente SÍ tiene nivel_3, pero el doc de Operaciones no se
    re-enriqueció (ingestado antes de segmentar). Un backfill (re-correr el enrich)
    lo llena.
  - SIN COMITENTE — la cuenta no está en Comitentes (propias [100], FCI, etc.) →
    nunca va a tener nivel_3 por esta vía.

NO escribe nada. Clasifica las cuentas con nivel_3 vacío en esos 3 baldes.

Uso:
    python -m scripts.diag_nivel3
"""
from __future__ import annotations

from collections import Counter

from core.mongo import get_mongo_client_read

_VACIO = {"$in": [None, ""]}


def main() -> int:
    db = get_mongo_client_read()
    ops = db["CashFlow"]["Operaciones"]
    comit = db["Clientes"]["Comitentes"]

    # Maps de Comitentes (chico, ~1.8k): id con nivel_3 y set de todos los ids.
    con_n3: set[str] = set()
    todos: set[str] = set()
    for d in comit.find({}, {"_id": 0, "id_cuenta": 1, "nivel_3": 1}):
        idc = str(d.get("id_cuenta") or "").strip()
        if not idc:
            continue
        todos.add(idc)
        if (d.get("nivel_3") or "").strip():
            con_n3.add(idc)

    total_ops = ops.estimated_document_count()
    print("=" * 64)
    print("DIAG nivel_3 vacío en CashFlow.Operaciones (read-only)")
    print(f"Operaciones: {total_ops:,} | Comitentes: {len(todos):,} "
          f"({len(con_n3):,} con nivel_3 cargado)")
    print("=" * 64)

    # Cuentas (id pelado) de Operaciones con nivel_3 vacío + cuántos docs cada una.
    por_cuenta = list(ops.aggregate([
        {"$match": {"nivel_3": _VACIO}},
        {"$group": {"_id": "$cuenta", "n": {"$sum": 1}}},
    ]))
    docs_vacios = sum(r["n"] for r in por_cuenta)
    print(f"\nDocs con nivel_3 vacío: {docs_vacios:,}  "
          f"({100*docs_vacios/total_ops:.1f}% del total) en {len(por_cuenta):,} cuentas\n")

    balde_docs: Counter = Counter()
    balde_ctas: Counter = Counter()
    muestras: dict[str, list[str]] = {"causa2_reenrich": [], "causa1_segmentar": [], "sin_comitente": []}
    for r in por_cuenta:
        cta = str(r["_id"] or "").strip()
        n = r["n"]
        if cta in con_n3:
            k = "causa2_reenrich"      # backfill lo arregla
        elif cta in todos:
            k = "causa1_segmentar"     # Comitente sin nivel_3 → segmentar primero
        else:
            k = "sin_comitente"        # propias / FCI / no comitente
        balde_docs[k] += n
        balde_ctas[k] += 1
        if len(muestras[k]) < 10:
            muestras[k].append(f"{cta} ({n} docs)")

    etiquetas = {
        "causa2_reenrich": "CAUSA 2 — Comitente CON nivel_3 → backfill (re-enrich) LO ARREGLA",
        "causa1_segmentar": "CAUSA 1 — Comitente SIN nivel_3 → segmentar primero (backfill no ayuda)",
        "sin_comitente": "SIN COMITENTE — cuenta propia/FCI/no comitente",
    }
    for k in ("causa2_reenrich", "causa1_segmentar", "sin_comitente"):
        print(f"── {etiquetas[k]}")
        print(f"   {balde_ctas[k]:,} cuentas · {balde_docs[k]:,} docs")
        if muestras[k]:
            print(f"   ej: {', '.join(muestras[k])}")
        print()

    print("Conclusión: si el grueso cae en CAUSA 2 → vale un backfill de enrich")
    print("(re-correr operaciones_informes.enriquecer). Si cae en CAUSA 1 → primero")
    print("hay que segmentar esos Comitentes. SIN COMITENTE no se arregla por acá.")
    print("\nread-only: no se escribió nada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
