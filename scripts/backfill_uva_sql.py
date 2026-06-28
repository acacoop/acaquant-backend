"""scripts/backfill_uva_sql.py — copia el histórico de UVA de Mongo a SQL.

Decomiso Mongo 2026-06-28: el reader de UVA pasó a SQL (macro.uva). Antes de
dropear `Trading.UVA` hay que copiar el valor (si no, se pierde y la
segmentación patrimonial queda sin UVA). Idempotente, read-only sobre Mongo.

`Trading.UVA` (Mongo) tiene `valor_uva` por doc; la fecha sale del campo `fecha`
si existe, o del timestamp del ObjectId (_id). Upsert por fecha en macro.uva.

    python -m scripts.backfill_uva_sql            # dry-run
    python -m scripts.backfill_uva_sql --apply
"""
from __future__ import annotations

import sys

from core.mongo import get_mongo_client_read
from core.postgres import get_pool


def main() -> int:
    apply = "--apply" in sys.argv
    col = get_mongo_client_read()["Trading"]["UVA"]
    rows: list[tuple[str, float]] = []
    for d in col.find({}):
        v = d.get("valor_uva")
        if v is None:
            continue
        fecha = d.get("fecha")
        if not fecha and d.get("_id") is not None and hasattr(d["_id"], "generation_time"):
            fecha = d["_id"].generation_time.strftime("%Y-%m-%d")
        if not fecha:
            continue
        try:
            rows.append((str(fecha)[:10], float(v)))
        except (TypeError, ValueError):
            continue

    print(f"backfill_uva_sql — {'APPLY' if apply else 'DRY-RUN'}: {len(rows)} filas")
    if apply and rows:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO uva (fecha, valor) VALUES (%s::date, %s) "
                "ON CONFLICT (fecha) DO UPDATE SET valor = EXCLUDED.valor",
                rows)
        print("Listo.")
    elif not apply:
        print("DRY-RUN. Re-correr con --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
