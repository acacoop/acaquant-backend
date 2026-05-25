"""diag_canje.py — Por qué serie_canje tarda 2.6s (100% Mongo).

serie_canje agrega Trading.TimeSales tick-by-tick de un año entero para 2
tickers (AL30C/AL30D), solo para sacar el cierre diario. AL30C/AL30D son
soberanos → NO están en SnapshotsCierre (solo persiste tasa_fija+cer), así que
el fix no es leer cierres. Hay que entender la query: ¿usa índice o escanea?
¿cuántos ticks procesa?

Este diag corre el .explain del pipeline real, lista los índices de TimeSales
y cuenta el volumen. Con eso se decide el fix: (a) índice compuesto
(ticker, timestamp) si hoy escanea, o (b) materializar un cierre diario para
los tickers de canje.

    python -m scripts.diag_canje
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from core.mongo import get_mongo_client_read

# Mismos tickers/rango que el default de serie_canje (AL30, últimos 365 días).
PARES = {
    "c": "MERV - XMEV - AL30C - 24hs",
    "d": "MERV - XMEV - AL30D - 24hs",
}


def main() -> None:
    cli = get_mongo_client_read()
    ts = cli["Trading"]["TimeSales"]

    hasta = date.today()
    desde = hasta - timedelta(days=365)
    inicio = datetime.combine(desde, datetime.min.time(), tzinfo=UTC)
    fin = datetime.combine(hasta + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    tickers = list(PARES.values())

    match = {"ticker": {"$in": tickers}, "price": {"$gt": 0},
             "timestamp": {"$gte": inicio, "$lt": fin}}

    print("=" * 70)
    print("ÍNDICES de Trading.TimeSales")
    print("=" * 70)
    for name, spec in ts.index_information().items():
        print(f"  {name}: {spec.get('key')}")

    print("\n" + "=" * 70)
    print("VOLUMEN — ticks que matchea la query de canje (1 año, 2 tickers)")
    print("=" * 70)
    n = ts.count_documents(match)
    print(f"  ticks a procesar: {n:,}")
    for tk in tickers:
        c = ts.count_documents({**match, "ticker": tk})
        print(f"    {tk}: {c:,}")

    print("\n" + "=" * 70)
    print("EXPLAIN del pipeline (executionStats)")
    print("=" * 70)
    pipeline = [
        {"$match": match},
        {"$addFields": {"fecha": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}}}},
        {"$sort": {"timestamp": -1}},
        {"$group": {"_id": {"ticker": "$ticker", "fecha": "$fecha"}, "price": {"$first": "$price"}}},
    ]
    exp = cli["Trading"].command(
        "aggregate", "TimeSales", pipeline=pipeline, explain=True,
    )
    # Buscar el stage de input y el plan ganador (shape varía por versión Mongo).
    stages = exp.get("stages") or []
    if stages:
        cursor = stages[0].get("$cursor", {})
        plan = cursor.get("queryPlanner", {}).get("winningPlan", {})
        execu = cursor.get("executionStats", {})
    else:
        plan = exp.get("queryPlanner", {}).get("winningPlan", {})
        execu = exp.get("executionStats", {})

    def _stage_names(p, acc):
        if not isinstance(p, dict):
            return
        if "stage" in p:
            acc.append(p["stage"])
        for k in ("inputStage", "inputStages"):
            v = p.get(k)
            if isinstance(v, list):
                for s in v:
                    _stage_names(s, acc)
            elif v:
                _stage_names(v, acc)

    names: list[str] = []
    _stage_names(plan, names)
    print(f"  stages del plan: {' → '.join(names) or '(no parseable, ver dump)'}")
    print(f"  docs examinados: {execu.get('totalDocsExamined', '?'):,}" if isinstance(execu.get('totalDocsExamined'), int) else f"  docs examinados: {execu.get('totalDocsExamined', '?')}")
    print(f"  keys examinadas: {execu.get('totalKeysExamined', '?')}")
    print(f"  nReturned:       {execu.get('nReturned', '?')}")
    print(f"  tiempo (ms):     {execu.get('executionTimeMillis', '?')}")

    print("\nLectura:")
    print("  • COLLSCAN arriba → no hay índice útil; un índice (ticker,timestamp)")
    print("    debería bajar 'docs examinados' a ~lo que devuelve.")
    print("  • Si IXSCAN pero docs_examinados >> nReturned → el costo es agrupar")
    print("    un año de ticks; ahí conviene materializar cierre diario de canje.")


if __name__ == "__main__":
    main()
