"""scripts/backfill_dolar_sql.py — rellena el histórico SQL del dólar desde Mongo.

CONTEXTO (decomiso Mongo, 2026-06-28): el motor de dólar pasó a SQL-native. Pero el
histórico SQL `valuaciones.dolar` venía del viejo `sync_dolar`, que copiaba SOLO
`timestamp + mep` → las columnas ccl/canje/al30* quedaron NULL. Y `dolar_oficial_live`
es tabla nueva (vacía hasta que postee la PC de oficina). Con el mercado cerrado,
la API cae al histórico → MEP muestra pero CCL/canje/oficial no.

Este backfill copia de Mongo (que tiene los docs COMPLETOS) a SQL:
  1. Valuaciones.Dolar       → valuaciones.dolar           (upsert por timestamp; suma ccl/canje/al30)
  2. Valuaciones.DolarOficialLive → valuaciones.dolar_oficial_live (upsert por instrumento)

Idempotente (UPSERT), batcheado, read-only sobre Mongo. Seguro de re-correr.

    python -m scripts.backfill_dolar_sql            # dry-run (cuenta, no escribe)
    python -m scripts.backfill_dolar_sql --apply    # escribe SQL
"""
from __future__ import annotations

import sys
import time

from core.mongo import get_mongo_client_read
from core.pg_mirror import write_native

_BATCH = 2000


def _backfill_dolar(apply: bool) -> int:
    """Valuaciones.Dolar → valuaciones.dolar (upsert por timestamp, columnas completas)."""
    col = get_mongo_client_read()["Valuaciones"]["Dolar"]
    campos = ("timestamp", "mep", "ccl", "canje", "al30_offer", "al30d_bid", "al30c_bid")
    rows: list[dict] = []
    n = 0
    cur = col.find({}, {"_id": 0, **{c: 1 for c in campos}})
    for d in cur:
        ts = d.get("timestamp")
        if ts is None:
            continue
        # timestamp tal cual viene de Mongo (mismo formato que insertó sync_dolar →
        # el upsert pega sobre la fila existente y le agrega ccl/canje/al30).
        row = {"timestamp": ts}
        for c in campos[1:]:
            if d.get(c) is not None:
                row[c] = d[c]
        rows.append(row)
        if len(rows) >= _BATCH:
            if apply:
                write_native("dolar", ["timestamp"], rows)
            n += len(rows)
            rows = []
            time.sleep(0.05)   # throttle: no starvar al pool
    if rows:
        if apply:
            write_native("dolar", ["timestamp"], rows)
        n += len(rows)
    return n


def _backfill_oficial(apply: bool) -> int:
    """Valuaciones.DolarOficialLive → valuaciones.dolar_oficial_live (upsert por instrumento)."""
    col = get_mongo_client_read()["Valuaciones"]["DolarOficialLive"]
    rows: list[dict] = []
    for d in col.find({}, {"_id": 0, "data": 1, "updated_at": 1}):
        data = d.get("data")
        if not isinstance(data, dict) or not data.get("ticker"):
            continue
        rows.append({
            "ticker":          data.get("ticker"),
            "codigo_segmento": data.get("codigoSegmento"),
            "codigo_plazo":    data.get("codigoPlazo"),
            "data":            data,
            "updated_at":      d.get("updated_at"),
        })
    if apply and rows:
        write_native("dolar_oficial_live", ["ticker", "codigo_segmento", "codigo_plazo"], rows)
    return len(rows)


def main() -> int:
    apply = "--apply" in sys.argv
    modo = "APPLY (escribe SQL)" if apply else "DRY-RUN (solo cuenta)"
    print(f"backfill_dolar_sql — {modo}\n")

    n_dolar = _backfill_dolar(apply)
    print(f"  valuaciones.dolar          ← Valuaciones.Dolar:           {n_dolar:,} filas")
    n_of = _backfill_oficial(apply)
    print(f"  valuaciones.dolar_oficial_live ← Valuaciones.DolarOficialLive: {n_of:,} filas")

    print("\nListo." if apply else "\nDRY-RUN. Re-correr con --apply para escribir.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
