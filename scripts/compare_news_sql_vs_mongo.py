"""scripts/compare_news_sql_vs_mongo.py — gate de cutover NEWS.

Read-only. Corre los handlers de api/routers/news.py por las dos vías (Mongo y SQL)
sobre una grilla representativa y reporta diffs. Sale ≠0 si algo no coincide.

    python -m scripts.compare_news_sql_vs_mongo
"""
from __future__ import annotations

import os
import sys

from api.services import news_sql


def _norm_list(docs):
    """Lista de headlines → set de (url, fuente, categoria, titulo) para comparar
    contenido sin depender de tipos de fecha ni del orden exacto."""
    out = []
    for d in docs:
        out.append((d.get("url"), d.get("fuente"), d.get("categoria"), d.get("titulo")))
    return out


def _mongo_list(**kw):
    os.environ.pop("NEWS_SQL", None)
    from importlib import reload

    from api.routers import news as _n
    reload(_n)
    return _n.list_headlines(**kw)


def _mongo_stats(horas):
    os.environ.pop("NEWS_SQL", None)
    from importlib import reload

    from api.routers import news as _n
    reload(_n)
    return _n.stats(horas)


GRID = [
    {},
    {"limit": 10},
    {"limit": 50, "skip": 10},
    {"fuente": None, "limit": 20},
]


def main() -> int:
    fails = 0
    # list_headlines
    for kw in GRID:
        full = {"desde": None, "hasta": None, "fuente": None, "categoria": None,
                "keyword": None, "limit": 100, "skip": 0, **kw}
        m = _norm_list(_mongo_list(**full))
        s = _norm_list(news_sql.list_headlines(**full))
        # mismo orden (ambos fecha desc) y mismo contenido
        if m != s:
            fails += 1
            print(f"FAIL list {kw}: mongo={len(m)} sql={len(s)} "
                  f"first_diff={[(a, b) for a, b in zip(m, s) if a != b][:1]}")
        else:
            print(f"OK   list {kw}: n={len(m)}")

    # stats
    for horas in (24, 72, 168):
        m = _mongo_stats(horas)
        s = news_sql.stats(horas)
        mb = {r["fuente"]: r["count"] for r in m["por_fuente"]}
        sb = {r["fuente"]: r["count"] for r in s["por_fuente"]}
        if m["total"] != s["total"] or mb != sb:
            fails += 1
            print(f"FAIL stats {horas}h: mongo_total={m['total']} sql_total={s['total']}")
        else:
            print(f"OK   stats {horas}h: total={m['total']} fuentes={len(mb)}")

    print(f"\n{'TODO OK' if not fails else f'{fails} DIFFS'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
