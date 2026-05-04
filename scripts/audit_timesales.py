"""Audit read-only de Trading.TimeSales para dimensionar migración a TS Collection.

Reúne en un solo output todo lo necesario para decidir granularity, ventana
de cutover, tiempo estimado de copia, y validar que el schema es compatible
con Mongo Time Series Collections.

Uso:
    python -m scripts.audit_timesales

Read-only. NO toca data. Pegale el output a Claude para que arme el plan
de migración.
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read

DB_NAME = "Trading"
COL_NAME = "TimeSales"


def _line(c: str = "─", n: int = 70) -> str:
    return c * n


def main() -> int:
    client = get_mongo_client_read()
    db = client[DB_NAME]
    col = db[COL_NAME]

    print(_line("="))
    print(f" AUDIT — {DB_NAME}.{COL_NAME}")
    print(_line("="))

    # 1) Versión de Mongo
    print("\n[1] Versión de Mongo")
    try:
        info = client.server_info()
        print(f"  version: {info.get('version')}")
    except Exception as e:
        print(f"  ERROR: {e}")

    # 2) Stats de storage y count
    print("\n[2] Stats de storage y count")
    try:
        stats = db.command("collStats", COL_NAME, scale=1024 * 1024)  # MB
        print(f"  count             : {stats.get('count'):,}")
        print(f"  size (data, MB)   : {stats.get('size'):.2f}")
        print(f"  storageSize (MB)  : {stats.get('storageSize'):.2f}")
        print(f"  totalIndexSize MB : {stats.get('totalIndexSize'):.2f}")
        print(f"  totalSize (MB)    : {stats.get('totalSize'):.2f}")
        print(f"  avgObjSize (B)    : {stats.get('avgObjSize'):.0f}")
        print(f"  nindexes          : {stats.get('nindexes')}")
    except Exception as e:
        print(f"  ERROR collStats: {e}")

    # 3) Tipo del campo timestamp (CRÍTICO para TS)
    print("\n[3] Tipo del campo `timestamp` (debe ser `date` para TS Collection)")
    try:
        sample_type = list(
            col.aggregate(
                [{"$limit": 1}, {"$project": {"tipo": {"$type": "$timestamp"}, "_id": 0}}]
            )
        )
        tipo = (sample_type[0] if sample_type else {}).get("tipo", "??")
        marker = "✓ OK para TS" if tipo == "date" else "✗ NO COMPATIBLE — hay que transformar"
        print(f"  tipo: {tipo!r}    {marker}")
    except Exception as e:
        print(f"  ERROR: {e}")

    # 4) Schema de un doc sample
    print("\n[4] Doc de muestra (con _id incluido para ver shape real)")
    try:
        doc = col.find_one()
        if doc:
            for k, v in doc.items():
                v_repr = repr(v)
                if len(v_repr) > 80:
                    v_repr = v_repr[:77] + "..."
                print(f"  {k}: {v_repr}    [{type(v).__name__}]")
        else:
            print("  (colección vacía)")
    except Exception as e:
        print(f"  ERROR: {e}")

    # 5) Indices actuales (van a recrearse en la TS post-migración)
    print("\n[5] Indices actuales")
    try:
        for ix in col.list_indexes():
            ix = dict(ix)
            name = ix.pop("name", "?")
            keys = ix.pop("key", {})
            extras = {k: v for k, v in ix.items() if k not in ("v", "ns")}
            extras_s = f"  {extras}" if extras else ""
            print(f"  - {name}: {dict(keys)}{extras_s}")
    except Exception as e:
        print(f"  ERROR: {e}")

    # 6) Distinct tickers (cuántas series)
    print("\n[6] Cantidad de tickers distintos")
    try:
        n = len(col.distinct("ticker"))
        print(f"  tickers únicos: {n}")
    except Exception as e:
        print(f"  ERROR: {e}")

    # 7) Count por ventanas — tasa de crecimiento estimada
    print("\n[7] Count por ventana temporal (estimar tasa de crecimiento)")
    from datetime import UTC, datetime, timedelta
    ahora = datetime.now(UTC)
    ventanas = [
        ("últimas 24h",   ahora - timedelta(days=1)),
        ("últimos 7d",    ahora - timedelta(days=7)),
        ("últimos 30d",   ahora - timedelta(days=30)),
        ("últimos 365d",  ahora - timedelta(days=365)),
    ]
    for label, desde in ventanas:
        try:
            n = col.count_documents({"timestamp": {"$gte": desde}})
            print(f"  {label:18s}: {n:>12,} docs")
        except Exception as e:
            print(f"  {label:18s}: ERROR {e}")

    # 8) Min/max timestamp (rango total de la data)
    print("\n[8] Rango temporal completo")
    try:
        first = col.find_one({}, sort=[("timestamp", 1)], projection={"timestamp": 1, "_id": 0})
        last = col.find_one({}, sort=[("timestamp", -1)], projection={"timestamp": 1, "_id": 0})
        print(f"  primer ts: {first.get('timestamp') if first else None}")
        print(f"  último ts: {last.get('timestamp')  if last  else None}")
    except Exception as e:
        print(f"  ERROR: {e}")

    print()
    print(_line("="))
    print(" Pegale este output completo a Claude para diseñar la migración.")
    print(_line("="))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
