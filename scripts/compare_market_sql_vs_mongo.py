"""scripts/compare_market_sql_vs_mongo.py — gate de cutover MARKET (quotes + calendar).

Read-only. Corre los handlers de api/routers/market.py por las dos vías (Mongo y SQL)
sobre una grilla representativa y reporta diffs. Sale ≠0 si algo no coincide.
Correr DESPUÉS de aplicar el ALTER de `evt_ts` en Supabase + un sync (los docs de
calendar necesitan la columna nueva poblada).

    python -m scripts.compare_market_sql_vs_mongo
"""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta

from api.services import market_sql


def _norm(v):
    """datetime → ISO recursivo, para comparar el path Mongo (datetimes vivos)
    contra el SQL (jsonb ya serializado) sin falsos diffs de tipo."""
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_norm(x) for x in v]
    return v


def _key(docs):
    """Lista de docs → lista ordenada de strings canónicos (orden-independiente
    para empates: Mongo no define orden entre docs con el mismo sort key)."""
    return sorted(json.dumps(_norm(d), sort_keys=True, default=str) for d in docs)


def _mongo():
    os.environ.pop("MARKET_SQL", None)
    from api.routers import market as m
    return m


def main() -> int:
    fails = 0
    m = _mongo()

    # /quotes — todos y por subset de símbolos (el subset sale de los datos reales).
    todos_mongo = m.quotes(symbols=None)
    todos_sql = market_sql.quotes(None)
    casos = [("todos", None)]
    syms = [d["symbol"] for d in todos_mongo[:3] if d.get("symbol")]
    if syms:
        casos.append((f"subset {syms}", ",".join(syms)))
    for label, arg in casos:
        mm = todos_mongo if arg is None else m.quotes(symbols=arg)
        ss = todos_sql if arg is None else market_sql.quotes(
            [s.strip().upper() for s in arg.split(",")])
        if _key(mm) != _key(ss):
            fails += 1
            solo_m = set(_key(mm)) - set(_key(ss))
            solo_s = set(_key(ss)) - set(_key(mm))
            print(f"FAIL quotes {label}: mongo={len(mm)} sql={len(ss)} "
                  f"solo_mongo={list(solo_m)[:1]} solo_sql={list(solo_s)[:1]}")
        else:
            print(f"OK   quotes {label}: n={len(mm)}")

    # /calendar/economic — rangos / importancia / país.
    hoy = datetime.now(UTC).date().isoformat()
    en_60 = (datetime.now(UTC) + timedelta(days=60)).date().isoformat()
    GRID = [
        {},
        {"desde": hoy, "hasta": en_60},
        {"importancia": 2},
        {"importancia": 3, "country": "US"},
        {"country": "AR"},
        {"limit": 25},
    ]
    for kw in GRID:
        full = {"desde": None, "hasta": None, "importancia": 0, "country": None,
                "limit": 500, **kw}
        mm = m.calendar_economic(**full)
        d_desde = m._parse(full["desde"]) or datetime.now(UTC)
        d_hasta = m._parse(full["hasta"]) or (datetime.now(UTC) + timedelta(days=30))
        ss = market_sql.calendar_economic(
            desde=d_desde, hasta=d_hasta, importancia=full["importancia"],
            country=full["country"], limit=full["limit"])
        if _key(mm) != _key(ss):
            fails += 1
            solo_m = set(_key(mm)) - set(_key(ss))
            solo_s = set(_key(ss)) - set(_key(mm))
            print(f"FAIL calendar {kw}: mongo={len(mm)} sql={len(ss)} "
                  f"solo_mongo={list(solo_m)[:1]} solo_sql={list(solo_s)[:1]}")
        else:
            print(f"OK   calendar {kw}: n={len(mm)}")

    print(f"\n{'TODO OK' if not fails else f'{fails} DIFFS'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
