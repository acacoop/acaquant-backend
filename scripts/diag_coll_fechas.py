"""Diag READ-ONLY: campos de fecha + índices de las colecciones de salida de jobs.

Los jobs que NO escriben en Manager.JobRuns se miden por la última escritura a su
colección. Este diag muestra, por colección, los campos tipo-fecha del último doc
+ los índices, para elegir el campo correcto E INDEXADO (query barata, sin
COLLSCAN cada 10s) en api/services/diagnostico_registry.py.

Read-only y barato: un find_one + list_indexes por colección.

Uso:
    python -m scripts.diag_coll_fechas
"""
from __future__ import annotations

from datetime import date, datetime

from core.mongo import get_mongo_client_read

COLECCIONES = [
    ("Trading", "SnapshotsCierre"),
    ("Trading", "FairValueResiduos"),
    ("Trading", "FitParams"),
    ("Trading", "ForwardsZscore"),
    ("Trading", "CanjeCierre"),
    ("Trading", "PreciosAcciones"),
    ("Trading", "AdrSnapshot"),
    ("Trading", "CER"),
    ("Valuaciones", "PnLTotalesCache"),
    ("Valuaciones", "ConsolidadoCuentas"),
    ("Market", "Quotes"),
    ("Market", "EconomicCalendar"),
]


def _es_fecha(v) -> bool:
    if isinstance(v, (datetime, date)):
        return True
    if isinstance(v, str) and 8 <= len(v) <= 30 and ("-" in v or "/" in v):
        return v[:4].isdigit() or v[:2].isdigit()
    return False


def main() -> int:
    cli = get_mongo_client_read()
    for db, coll in COLECCIONES:
        c = cli[db][coll]
        doc = c.find_one()
        if not doc:
            print(f"\n{db}.{coll}: VACÍA / no existe")
            continue
        idx = {ix["name"]: list(ix["key"].keys()) for ix in c.list_indexes()}
        fechas = {k: str(v)[:19] for k, v in doc.items() if _es_fecha(v)}
        print(f"\n{db}.{coll}  (~{c.estimated_document_count()} docs)")
        print(f"  campos fecha: {fechas}")
        print(f"  índices: {idx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
