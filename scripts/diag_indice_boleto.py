"""scripts/diag_indice_boleto.py — por qué el upsert por `boleto` en Operaciones
hace COLLSCAN de 488k (root cause del CPU 100%, confirmado por el Profiler).

El Full Parsed Log mostró: update {boleto: X} upsert=true → planSummary COLLSCAN,
keysExamined 0, docsExamined 488314. O sea: la query por boleto NO usa el índice
uq_boleto. La ingesta horaria (operaciones_informes) hace cientos de estos upserts
por corrida → cada uno escanea 488k → CPU 100%.

Este diag (read-only) confirma POR QUÉ no se usa el índice:
  1. Spec completo de todos los índices → si uq_boleto tiene `collation` (la causa
     clásica: índice con collation ≠ query default → no se puede usar).
  2. explain() de un find({boleto}) → IXSCAN[uq_boleto] (OK) vs COLLSCAN (problema).

Read-only. Correr:
    python -m scripts.diag_indice_boleto
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read


def main() -> int:
    coll = get_mongo_client_read()["CashFlow"]["Operaciones"]

    print("══ 1. Índices de CashFlow.Operaciones (spec completo) ══")
    for ix in coll.list_indexes():
        d = dict(ix)
        flags = []
        if d.get("unique"):
            flags.append("unique")
        if d.get("collation"):
            flags.append(f"COLLATION={d['collation'].get('locale')}")
        if d.get("partialFilterExpression"):
            flags.append("partial")
        print(f"  {d.get('name', '?'):30} key={list(d.get('key', {}).items())}  {' '.join(flags)}")

    print("\n══ 2. explain() de find({boleto: ...}) ══")
    plan = coll.find({"boleto": "BOL 2026090070"}).explain()
    qp = plan.get("queryPlanner", {})
    stage = qp.get("winningPlan", {})
    chain = []
    while stage:
        s = stage.get("stage")
        if s:
            chain.append(s + (f"[{stage['indexName']}]" if stage.get("indexName") else ""))
        stage = stage.get("inputStage")
    print(f"  winningPlan: {' → '.join(chain) or '?'}")
    qcol = qp.get("collation")
    if qcol:
        print(f"  collation de la query: {qcol.get('locale')}")

    usa = any("IXSCAN" in c for c in chain)
    print("\n→ " + (
        "✓ find({boleto}) USA el índice → el find anda; el problema es el upsert/plan cache."
        if usa else
        "❌ find({boleto}) hace COLLSCAN → uq_boleto NO se puede usar para esta query.\n"
        "  Si arriba uq_boleto muestra COLLATION → ESA es la causa: hay que RECREARLO sin\n"
        "  collation (key boleto:1, unique, collation simple). Cada upsert pasa de escanear\n"
        "  488k a un lookup de 1 doc → mata el CPU 100%."
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
