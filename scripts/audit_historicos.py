"""Audit READ-ONLY del estado de las colecciones de "histórico" relevantes
para decidir el plan de migración a SnapshotsCierre.

Optimizado para mínimo costo Mongo:
- count_documents con filtros indexados (no scans).
- aggregate solo sobre colecciones chicas (Curvas: ~62 docs,
  SnapshotsCierre: probablemente <1000 docs, MarketSnapshot: ~62 docs).
- find_one con sort por índice existente para min/max timestamps.
- Sin reads sobre TimeSales que no usen índice.

Uso:
    python -m scripts.audit_historicos
"""
from __future__ import annotations

import logging

from core.mongo import get_mongo_client_read

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("audit")

DB = "Trading"
CURVAS_VALIDAS = ("tasa_fija", "cer", "soberanos", "tamar", "dolar_linked")


def _line(c: str = "─", n: int = 70) -> str:
    return c * n


def main() -> int:
    client = get_mongo_client_read()
    db = client[DB]

    print(_line("="))
    print(" AUDIT — Estado de colecciones de histórico")
    print(_line("="))

    # ─────────────────────────────────────────────────────────────────
    # [1] Trading.Curvas — universo de tickers por curva
    # ─────────────────────────────────────────────────────────────────
    print("\n[1] Trading.Curvas — tickers por curva (catálogo)")
    curvas_docs = list(db["Curvas"].find(
        {}, {"_id": 0, "ticker": 1, "ticker_corto": 1, "curva": 1}
    ))
    curvas_por_tipo: dict[str, list[str]] = {}
    sin_curva = 0
    for d in curvas_docs:
        c = d.get("curva")
        t = d.get("ticker")
        if c and t:
            curvas_por_tipo.setdefault(c, []).append(t)
        elif t:
            sin_curva += 1

    for curva in CURVAS_VALIDAS:
        n = len(curvas_por_tipo.get(curva, []))
        print(f"    {curva:15s} {n:>3} tickers")
    if sin_curva:
        print(f"    (sin curva)     {sin_curva:>3} tickers")
    total_curvas = sum(len(v) for v in curvas_por_tipo.values())
    print(f"    TOTAL           {total_curvas:>3} tickers en Trading.Curvas")

    # ─────────────────────────────────────────────────────────────────
    # [2] Trading.SnapshotsCierre — cobertura
    # ─────────────────────────────────────────────────────────────────
    print("\n[2] Trading.SnapshotsCierre — cobertura actual")
    col_sc = db["SnapshotsCierre"]
    try:
        stats = db.command("collStats", "SnapshotsCierre", scale=1024 * 1024)
        sc_total = stats.get("count", 0)
        sc_size_mb = stats.get("totalSize", 0)
    except Exception:
        sc_total = col_sc.count_documents({})
        sc_size_mb = None

    print(f"    Docs totales: {sc_total:,}")
    if sc_size_mb is not None:
        print(f"    Storage:      {sc_size_mb:.2f} MB")

    if sc_total == 0:
        print("    (colección vacía — nunca corrió snapshot_cierre)")
    else:
        # Rango temporal global
        first = col_sc.find_one({}, {"_id": 0, "ts_cierre": 1}, sort=[("ts_cierre", 1)])
        last = col_sc.find_one({}, {"_id": 0, "ts_cierre": 1}, sort=[("ts_cierre", -1)])
        print(f"    Rango:        {first.get('ts_cierre')}  →  {last.get('ts_cierre')}")

        # Por curva: count + rango + tickers únicos
        print("\n    Por curva:")
        agg = list(col_sc.aggregate([
            {"$group": {
                "_id": "$curva",
                "count":      {"$sum": 1},
                "primer_ts":  {"$min": "$ts_cierre"},
                "ultimo_ts":  {"$max": "$ts_cierre"},
                "tickers":    {"$addToSet": "$ticker"},
                "fechas":     {"$addToSet": "$ts_cierre"},
            }},
            {"$sort": {"_id": 1}},
        ]))
        for r in agg:
            curva = r["_id"]
            n_tickers = len(r["tickers"])
            n_fechas = len(r["fechas"])
            esperados = len(curvas_por_tipo.get(curva, []))
            print(f"      {curva:15s} {r['count']:>5} docs · "
                  f"{n_fechas:>3} días distintos · "
                  f"{n_tickers}/{esperados} tickers · "
                  f"{r['primer_ts']} → {r['ultimo_ts']}")

    # ─────────────────────────────────────────────────────────────────
    # [3] Trading.TimeSales — estado del enriquecimiento
    # ─────────────────────────────────────────────────────────────────
    print("\n[3] Trading.TimeSales — estado del enriquecimiento")
    col_ts = db["TimeSales"]
    # count_documents con índice ticker_1_duration_1_timestamp_-1 → barato.
    n_total = col_ts.count_documents({})
    n_enriq = col_ts.count_documents({"duration": {"$exists": True, "$ne": None}})
    n_sin = col_ts.count_documents({"duration": {"$exists": False}})
    n_null = col_ts.count_documents({"duration": None})
    print(f"    Total:                    {n_total:>10,}")
    if n_total > 0:
        pct_enriq = 100 * n_enriq / n_total
        print(f"    Con duration (no null):   {n_enriq:>10,}  ({pct_enriq:.1f}%)")
        print(f"    Sin duration ($exists):   {n_sin:>10,}")
        print(f"    duration: null:           {n_null:>10,}")

        # Rango de docs enriquecidos (usa índice ticker+duration+timestamp)
        primer = col_ts.find_one(
            {"duration": {"$exists": True, "$ne": None}},
            {"_id": 0, "timestamp": 1},
            sort=[("timestamp", 1)],
        )
        ultimo = col_ts.find_one(
            {"duration": {"$exists": True, "$ne": None}},
            {"_id": 0, "timestamp": 1},
            sort=[("timestamp", -1)],
        )
        if primer and ultimo:
            print(f"    Rango enriquecido:        {primer['timestamp']}  →  {ultimo['timestamp']}")

    # ─────────────────────────────────────────────────────────────────
    # [4] Trading.MarketSnapshot — estado actual
    # ─────────────────────────────────────────────────────────────────
    print("\n[4] Trading.MarketSnapshot — estado actual")
    col_ms = db["MarketSnapshot"]
    n_ms_total = col_ms.count_documents({})
    n_ms_last = col_ms.count_documents({"metrics.last_price": {"$gt": 0}})
    n_ms_tea = col_ms.count_documents({"metrics.TEA": {"$exists": True, "$ne": None}})
    print(f"    Docs totales:                  {n_ms_total:>3}")
    print(f"    Con metrics.last_price > 0:    {n_ms_last:>3}")
    print(f"    Con metrics.TEA poblado:       {n_ms_tea:>3}")

    # ─────────────────────────────────────────────────────────────────
    # [5] Diagnóstico — qué falta para migrar consumers
    # ─────────────────────────────────────────────────────────────────
    print(f"\n{_line('=')}")
    print(" DIAGNÓSTICO")
    print(_line("="))

    print("\nCobertura SnapshotsCierre por curva:")
    sc_por_curva = {r["_id"]: r for r in agg} if sc_total > 0 else {}
    sin_cobertura = []
    parcial = []
    completa = []
    for curva in CURVAS_VALIDAS:
        esperados = len(curvas_por_tipo.get(curva, []))
        if esperados == 0:
            continue
        if curva not in sc_por_curva:
            sin_cobertura.append(curva)
        else:
            n_dias = len(sc_por_curva[curva]["fechas"])
            if n_dias < 5:  # arbitrario, indicador de "muy poco"
                parcial.append((curva, n_dias))
            else:
                completa.append((curva, n_dias))

    if sin_cobertura:
        print("  ❌ SIN cobertura (snapshot_cierre nunca corrió para esta curva):")
        for c in sin_cobertura:
            esperados = len(curvas_por_tipo.get(c, []))
            print(f"     - {c} ({esperados} tickers en Curvas)")

    if parcial:
        print("  ⚠ Cobertura PARCIAL (<5 días):")
        for c, n in parcial:
            print(f"     - {c}: solo {n} días")

    if completa:
        print("  ✓ Cobertura razonable:")
        for c, n in completa:
            print(f"     - {c}: {n} días de cierre")

    print(f"\nTimeSales tiene {n_enriq:,} trades enriquecidos hasta {ultimo['timestamp'] if ultimo else 'N/A'}.")
    print("Estos sirven como FUENTE para backfillear SnapshotsCierre de fechas pasadas")
    print("(la lógica vieja agrega TimeSales del día por (ticker, fecha) → último trade).")

    print(f"\n{_line('=')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
