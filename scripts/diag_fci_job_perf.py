"""Diag READ-ONLY: por qué jobs.fci_bilateral tarda tanto.

Mide (REGLA #2): tamaño de NegocioMovimientos, tamaño del subset FCI que lee el
job, y el PLAN de la query del job (COLLSCAN vs IXSCAN + docs examinados). Con
eso sabemos si el problema es un scan de toda la colección y cómo arreglarlo.

NO escribe nada. Solo lectura.

Uso:
    python -m scripts.diag_fci_job_perf
"""
from __future__ import annotations

import time

from core.mongo import get_mongo_client_read

_CATS_LIQ = ["suscripcion_fci", "rescate_fci"]
_CATS_SOL = ["solicitud_suscripcion_fci", "solicitud_rescate_fci"]

_Q = {"$or": [
    {"categoria": {"$in": _CATS_LIQ}, "comprobante": {"$regex": "^CL", "$options": "i"}},
    {"categoria": {"$in": _CATS_SOL}},
]}


def _find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


def main() -> None:
    db = get_mongo_client_read()["CashFlow"]
    mov = db["NegocioMovimientos"]

    print("Contando NegocioMovimientos…")
    total = mov.estimated_document_count()
    print(f"  total (estimado): {total}")

    print("\nÍndices de NegocioMovimientos:")
    for ix in mov.list_indexes():
        print("  ", ix.get("name"), dict(ix.get("key", {})))

    print("\nexplain del find del job (executionStats):")
    ex = db.command("explain",
                    {"find": "NegocioMovimientos", "filter": _Q,
                     "projection": {"_id": 0, "comprobante": 1, "categoria": 1}},
                    verbosity="executionStats")
    win = _find_key(ex, "winningPlan") or {}
    plan = str(win)
    kind = "COLLSCAN" if "COLLSCAN" in plan else ("IXSCAN" if "IXSCAN" in plan else "?")
    print(f"  plan={kind}")
    print(f"  index={_find_key(win, 'indexName')}")
    print(f"  totalDocsExaminados={_find_key(ex, 'totalDocsExamined')}")
    print(f"  totalKeysExaminadas={_find_key(ex, 'totalKeysExamined')}")
    print(f"  nReturned={_find_key(ex, 'nReturned')}")
    print(f"  executionTimeMillis={_find_key(ex, 'executionTimeMillis')}")

    print("\nCronometrando el find real (solo leer, sin escribir)…")
    t0 = time.perf_counter()
    n = 0
    for _ in mov.find(_Q, {"_id": 0, "comprobante": 1}):
        n += 1
    print(f"  leídos {n} docs en {(time.perf_counter() - t0):.1f}s")

    print("\n(read-only: no se escribió nada)")


if __name__ == "__main__":
    main()
