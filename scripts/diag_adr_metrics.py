"""diag_adr_metrics.py — bisecciona dónde se rompe el feed ADR del Scanner.

Si la tabla muestra "--" en TODAS las columnas ADR, el problema vive en
uno de:
  [A] Trading.PreciosAcciones está vacía o no se replicó al secondary.
  [B] El find() del service no matchea (typo de field, regex raro).
  [C] _adr_metrics_para_todos() falla silenciosa.
  [D] get_cedears_scanner() funciona pero la API sirve cache viejo.

Este script chequea los 4 niveles SIN pasar por HTTP.

Uso:
    python -m scripts.diag_adr_metrics
"""
from __future__ import annotations

import json
from datetime import datetime

from core.mongo import get_mongo_client


def _short(v):
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def run():
    print("=" * 100)
    print("DIAG ADR metrics — bisección del problema")
    print("=" * 100)

    # ── [A] Estado de Trading.PreciosAcciones ─────────────────────
    print("\n[A] Trading.PreciosAcciones (PRIMARY client)")
    client = get_mongo_client()
    db = client["Trading"]
    if "PreciosAcciones" not in db.list_collection_names():
        print("    ✗ Colección NO EXISTE.")
        return
    col = db["PreciosAcciones"]
    total = col.estimated_document_count()
    print(f"    Total docs (estimate): {total}")

    # Distinct tickers
    tickers_distinct = sorted(col.distinct("ticker"))
    print(f"    Tickers distintos: {len(tickers_distinct)}")
    print(f"    → {tickers_distinct}")

    # Una muestra
    sample = col.find_one({"ticker": "NVDA"}, sort=[("fecha", -1)])
    print(f"\n    Sample NVDA último doc:")
    if sample:
        for k, v in sample.items():
            print(f"      {k:<12s}: {_short(v)}")
    else:
        print("      ✗ Sin docs para NVDA")
        return

    # ── [B] Mismo find que usa el service, lado primary ──────────
    print("\n[B] find({'ticker': {'$in': ['NVDA','AMD']}}) — primary client")
    docs_primary = list(col.find(
        {"ticker": {"$in": ["NVDA", "AMD"]}},
        projection={"_id": 0, "ticker": 1, "fecha": 1, "close": 1},
    ))
    print(f"    docs devueltos: {len(docs_primary)}")
    if docs_primary:
        print(f"    primer doc: {docs_primary[0]}")

    # ── [C] Mismo find pero por el READ client (lo que usa la API) ──
    print("\n[C] find() via READ client (api/db.get_db_trading)")
    from api.db import get_db_trading
    db_read = get_db_trading()
    docs_read = list(db_read["PreciosAcciones"].find(
        {"ticker": {"$in": ["NVDA", "AMD"]}},
        projection={"_id": 0, "ticker": 1, "fecha": 1, "close": 1},
    ))
    print(f"    docs devueltos: {len(docs_read)}")
    if docs_read:
        print(f"    primer doc: {docs_read[0]}")
    if len(docs_read) == 0 and len(docs_primary) > 0:
        print("    ⚠ READ devuelve 0 pero PRIMARY devuelve docs → secondary sin replicar todavía.")

    # ── [D] Llamada directa al helper del service ────────────────
    print("\n[D] _adr_metrics_para_todos(['NVDA', 'AMD']) — directo, sin cache")
    from api.cache import clear_cache
    clear_cache()
    from api.services.scanner import _adr_metrics_para_todos
    res = _adr_metrics_para_todos(["NVDA", "AMD"])
    print(json.dumps(res, default=_short, indent=2))

    # ── [E] Llamada al service completo ───────────────────────────
    print("\n[E] get_cedears_scanner() — primer row")
    clear_cache()
    from api.services.scanner import get_cedears_scanner
    out = get_cedears_scanner()
    print(f"    {len(out)} rows total")
    if out:
        print("    Primer row (campos ADR):")
        row0 = out[0]
        for k in ("ticker_corto", "adr_last", "adr_fecha", "adr_vs_1d_pct",
                  "adr_ret_7d_pct", "adr_ret_mtd_pct", "adr_ret_ytd_pct"):
            print(f"      {k:<20s}: {row0.get(k)}")

    print("\n" + "=" * 100)
    print("LECTURA")
    print("=" * 100)
    print("• Si [A] total=0 → backfill no escribió. Re-correr.")
    print("• Si [B] OK pero [C] vacío → lag de replicación secondary. Esperar 5-10s.")
    print("• Si [D] todos None aunque [C] tiene docs → bug en _adr_metrics_para_todos.")
    print("• Si [D] OK pero [E] vacío → bug en get_cedears_scanner (filter master).")
    print("• Si [E] OK con valores → el endpoint sirve bien, problema es cache/CDN.")


if __name__ == "__main__":
    run()
