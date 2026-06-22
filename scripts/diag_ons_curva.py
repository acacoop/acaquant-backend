"""diag_ons_curva.py — ¿con qué `curva` están en Trading.Curvas las ONs de BondsMaster?

Read-only. El backfill reveló que los 169 assets de BondsMaster están en Curvas por
ticker_corto pero NINGUNO con curva on_*. Esto muestra la curva REAL de cada uno para
entender el modelo antes de tocar nada.

    python -m scripts.diag_ons_curva
"""
from __future__ import annotations

from collections import Counter

from core.mongo import get_mongo_client_read


def main() -> int:
    trd = get_mongo_client_read()["Trading"]
    bm_assets = [b.get("asset") for b in trd["BondsMaster"].find({}, {"_id": 0, "asset": 1})
                 if b.get("asset")]
    curvas = {c.get("ticker_corto"): c for c in trd["Curvas"].find(
        {}, {"_id": 0, "ticker_corto": 1, "curva": 1, "sector": 1}) if c.get("ticker_corto")}

    print(f"═══ {len(bm_assets)} ONs de BondsMaster — su curva REAL en Trading.Curvas ═══\n")
    dist: Counter = Counter()
    no_curva = []
    muestra = []
    for a in bm_assets:
        c = curvas.get(a)
        if not c:
            no_curva.append(a)
            dist["(no está en Curvas)"] += 1
            continue
        cv = c.get("curva") or "(sin curva)"
        dist[cv] += 1
        if len(muestra) < 15:
            muestra.append((a, cv, c.get("sector")))

    print("Distribución por curva:")
    for cv, n in dist.most_common():
        print(f"  {cv:<22} {n:>4}")

    print("\nMuestra (asset · curva · sector):")
    for a, cv, s in muestra:
        print(f"  {a:<10} {cv:<22} {s or ''}")

    # ¿Cuántas curva on_* hay en total en Curvas (vengan o no de estos assets)?
    n_on = trd["Curvas"].count_documents({"curva": {"$regex": "^on"}})
    print(f"\nTotal docs con curva on_* en Curvas: {n_on}")
    if no_curva:
        print(f"Assets de BM que NO están en Curvas: {len(no_curva)} → {', '.join(no_curva[:10])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
