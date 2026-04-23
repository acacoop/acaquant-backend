"""Replica EXACTAMENTE el query del motor_curvas y muestra qué docs encuentra.

Si el motor lee top 200 ordenados DESC, este script muestra qué 200 son
y si los del backfill viejo aparecen o no. Si NO aparecen, hay un
problema con el índice o el ordenamiento.

Uso:
    python -m scripts.test_motor_query
"""
from __future__ import annotations

from collections import Counter

from core.mongo import get_mongo_client
from engines._curvas_loader import cargar_indexado_por_ticker

BATCH_SIZE = 200


def main() -> int:
    c = get_mongo_client()
    ts_col = c["Trading"]["TimeSales"]

    curvas_idx = cargar_indexado_por_ticker()
    tickers = list(curvas_idx.keys())
    print(f"Tickers indexados por motor_curvas: {len(tickers)}")

    # COUNT global de docs sin duration cuyo ticker está en la lista
    total_sin_dur = ts_col.count_documents(
        {"ticker": {"$in": tickers}, "duration": {"$exists": False}}
    )
    print(f"Docs sin duration con ticker en la lista: {total_sin_dur}")
    print()

    # MISMA query que el loop del motor
    docs = list(ts_col.find(
        {"ticker": {"$in": tickers}, "duration": {"$exists": False}},
        sort=[("timestamp", -1)],
        limit=BATCH_SIZE,
    ))
    print(f"Top {BATCH_SIZE} docs sin duration (orden DESC), tal cual los lee el motor:")
    if not docs:
        print("  (vacío) → el motor no tendría qué procesar.")
        return 0

    por_ticker = Counter(d["ticker"] for d in docs)
    rangos: dict[str, tuple] = {}
    for d in docs:
        tk = d["ticker"]
        ts = d["timestamp"]
        if tk not in rangos:
            rangos[tk] = (ts, ts)
        else:
            mn, mx = rangos[tk]
            rangos[tk] = (min(mn, ts), max(mx, ts))

    print(f"{'Ticker':<55}{'docs':>6}  {'rango'}")
    for tk, n in sorted(por_ticker.items(), key=lambda x: -x[1]):
        mn, mx = rangos[tk]
        print(f"{tk:<55}{n:>6}  {mn} → {mx}")

    print()
    print(f"Total leído: {len(docs)} (limite era {BATCH_SIZE})")
    print()

    # ¿Hay algún doc sin duration MÁS VIEJO que el más viejo del batch?
    if docs:
        mas_viejo_batch = min(d["timestamp"] for d in docs)
        n_mas_viejos = ts_col.count_documents({
            "ticker": {"$in": tickers},
            "duration": {"$exists": False},
            "timestamp": {"$lt": mas_viejo_batch},
        })
        print(f"Más viejo del batch: {mas_viejo_batch}")
        print(f"Docs sin duration MÁS VIEJOS que ese: {n_mas_viejos}")
        if n_mas_viejos > 0:
            print(
                "  → el motor los procesará en ciclos siguientes (sigue bajando "
                "según se enriquezcan los más recientes)."
            )

    # Sample de un GD del backfill: ¿está en el batch?
    gd30d = "MERV - XMEV - GD30D - 24hs"
    en_batch_gd = sum(1 for d in docs if d["ticker"] == gd30d)
    sin_dur_gd = ts_col.count_documents({"ticker": gd30d, "duration": {"$exists": False}})
    print()
    print(f"GD30D específico:")
    print(f"  total sin duration: {sin_dur_gd}")
    print(f"  en este batch:      {en_batch_gd}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
