"""scripts/fix_indice_boleto.py — recrea el índice de boleto para que el upsert lo USE.

Confirmado (diag_indice_boleto): uq_boleto es PARTIAL y find({boleto}) hace COLLSCAN
de 488k. La regla de Mongo: un índice parcial solo se usa si la query incluye su
`partialFilterExpression` — y el upsert de operaciones_informes hace {boleto: X} a
secas → no lo usa → escanea 488k cientos de veces/corrida → CPU 100%. Root cause.

Fix: crear un índice único PLANO sobre boleto (usable por la query de igualdad),
validar que el find lo usa, y recién ahí dropear el partial viejo → SIN ventana sin
restricción única. Construir el índice escanea 488k UNA vez → correr FUERA DE RUEDA.

    python -m scripts.fix_indice_boleto              # CHECK (default, NO toca nada)
    /root/TradingAV/deploy/run_job.sh fix_indice_boleto 20m \
        'cd /root/TradingAV && /root/TradingAV/venv/bin/python -m scripts.fix_indice_boleto --fix'
"""
from __future__ import annotations

import argparse

from pymongo import ASCENDING

from core.mongo import get_mongo_client

_OLD = "uq_boleto"
_NEW = "uq_boleto_full"


def _plan(coll) -> tuple[bool, str]:
    win = coll.find({"boleto": "BOL 2026090070"}).explain()["queryPlanner"]["winningPlan"]
    chain, st = [], win
    while st:
        s = st.get("stage")
        if s:
            chain.append(s + (f"[{st['indexName']}]" if st.get("indexName") else ""))
        st = st.get("inputStage")
    return any("IXSCAN" in c for c in chain), " → ".join(chain) or "?"


def run(fix: bool) -> int:
    coll = get_mongo_client()["CashFlow"]["Operaciones"]
    idx = {i["name"]: dict(i) for i in coll.list_indexes()}
    old = idx.get(_OLD, {})
    print(f"uq_boleto: presente={bool(old)} · partial={'partialFilterExpression' in old}")
    if old.get("partialFilterExpression"):
        print(f"  partialFilterExpression: {old['partialFilterExpression']}")
    malos = coll.count_documents(
        {"$or": [{"boleto": {"$exists": False}}, {"boleto": None}, {"boleto": ""}]})
    print(f"  docs sin boleto válido (null/ausente/''): {malos}")
    usa, chain = _plan(coll)
    print(f"  find({{boleto}}) hoy: {chain}  → {'IXSCAN ✓' if usa else 'COLLSCAN ❌'}")

    if not fix:
        print("\n[CHECK] No se tocó nada. Arreglar (FUERA DE RUEDA): --fix vía run_job.")
        if malos:
            print(f"  ⚠ {malos} docs sin boleto válido → un unique plano daría E11000. "
                  "Los vemos antes de --fix.")
        return 0

    if malos:
        print(f"\n❌ ABORTO: {malos} docs sin boleto válido → un índice unique plano daría "
              "E11000. Hay que limpiarlos/decidir primero. NO recreo a ciegas.")
        return 1

    if _NEW not in idx:
        print(f"\nCreando índice plano unique {_NEW} (escanea 488k una vez)…")
        coll.create_index([("boleto", ASCENDING)], unique=True, name=_NEW)
    usa, chain = _plan(coll)
    print(f"  find({{boleto}}) ahora: {chain}  → {'IXSCAN ✓' if usa else 'COLLSCAN ❌'}")
    if not usa:
        print("❌ El índice nuevo no se usa (raro). NO dropeo el viejo. Revisar a mano.")
        return 1
    if _OLD in idx:
        coll.drop_index(_OLD)
        print(f"  Drop {_OLD} (partial) OK.")
    print("\n✅ Cada upsert por boleto pasa de escanear 488k → lookup de 1 doc. CPU resuelto de raíz.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="recrea el índice (default: solo check)")
    args = ap.parse_args()
    return run(args.fix)


if __name__ == "__main__":
    raise SystemExit(main())
