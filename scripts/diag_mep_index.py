"""diag_mep_index.py — ¿get_mep_for_date escanea Valuaciones.Dolar o usa índice?

get_mep_for_date (api/services/_mep.py) hace, por cada fecha sin mep en el boleto:
    find_one({mep: {$ne: None}, timestamp: {$lte: target}}, sort=[(timestamp, -1)])
Lo llaman pnl, portfolio y valuaciones (~20 sitios). Si Dolar no tiene índice en
timestamp, cada llamada es un COLLSCAN → acelerar con un índice ayuda a todos de una.

Este diag lista índices, mide el tamaño y corre el explain de esa query.

    python -m scripts.diag_mep_index
"""
from __future__ import annotations

from datetime import datetime

from core.mongo import get_mongo_client_read


def main() -> None:
    cli = get_mongo_client_read()
    col = cli["Valuaciones"]["Dolar"]

    print("=" * 70)
    print("ÍNDICES de Valuaciones.Dolar")
    print("=" * 70)
    tiene_ts = False
    for name, spec in col.index_information().items():
        keys = spec.get("key")
        print(f"  {name}: {keys}")
        if keys and keys[0][0] == "timestamp":
            tiene_ts = True
    print(f"  total docs: {col.estimated_document_count():,}")
    print(f"  → índice que arranca en timestamp: {'SÍ' if tiene_ts else 'NO'}")

    target = datetime.fromisoformat("2025-06-01T23:59:59")
    exp = (
        col.find({"mep": {"$ne": None}, "timestamp": {"$lte": target}})
        .sort([("timestamp", -1)])
        .limit(1)
        .explain()
    )
    qp = exp.get("queryPlanner", {}).get("winningPlan", {})
    ex = exp.get("executionStats", {})
    names: list[str] = []

    def walk(p):
        if not isinstance(p, dict):
            return
        if "stage" in p:
            names.append(p["stage"])
        for k in ("inputStage", "inputStages"):
            v = p.get(k)
            if isinstance(v, list):
                for s in v:
                    walk(s)
            elif v:
                walk(v)

    walk(qp)

    print("\n" + "=" * 70)
    print("EXPLAIN — get_mep_for_date (find_one con sort timestamp desc)")
    print("=" * 70)
    print(f"  stages:          {' → '.join(names) or '?'}")
    print(f"  docs examinados: {ex.get('totalDocsExamined', '?')}")
    print(f"  keys examinadas: {ex.get('totalKeysExamined', '?')}")
    print(f"  nReturned:       {ex.get('nReturned', '?')}")
    print(f"  ms:              {ex.get('executionTimeMillis', '?')}")

    print("\nLectura:")
    print("  • COLLSCAN o docs_examinados alto → falta índice. Crear:")
    print('      db.Dolar.createIndex({timestamp: -1})')
    print("    acelera get_mep_for_date en pnl/portfolio/valuaciones sin tocar código.")
    print("  • IXSCAN con docs_examinados ~1 → ya está optimizado; el costo es")
    print("    la latencia de N round-trips (ahí el fix sería batch, no índice).")


if __name__ == "__main__":
    main()
