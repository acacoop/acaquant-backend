"""scripts/compare_curvas_sql_vs_mongo.py — gate de paridad del master RF antes de dropear.

Compara `Trading.Curvas` (Mongo) ↔ `mercado.curvas` (SQL) por conteo + set de
ticker_corto + spot-check de flujos. Read-only. Si da PARIDAD, es seguro dropear
Trading.Curvas (los ~20 readers + el loader de motores ya leen SQL).

    python -m scripts.compare_curvas_sql_vs_mongo
"""
from __future__ import annotations

import sys

from core.mongo import get_mongo_client_read
from core.postgres import get_pool


def main() -> int:
    mongo = {
        _s(d.get("ticker_corto")): d
        for d in get_mongo_client_read()["Trading"]["Curvas"].find({}, {"_id": 0})
        if _s(d.get("ticker_corto"))
    }
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker_corto, data FROM mercado.curvas")
        sql = {r[0]: r[1] for r in cur.fetchall() if r[0]}

    m_keys, s_keys = set(mongo), set(sql)
    print(f"Mongo Trading.Curvas : {len(m_keys)} ticker_corto")
    print(f"SQL   mercado.curvas : {len(s_keys)} ticker_corto\n")

    solo_mongo = sorted(m_keys - s_keys)
    solo_sql = sorted(s_keys - m_keys)
    if solo_mongo:
        print(f"⚠ EN MONGO Y NO EN SQL ({len(solo_mongo)}): {solo_mongo[:20]}")
    if solo_sql:
        print(f"⚠ EN SQL Y NO EN MONGO ({len(solo_sql)}): {solo_sql[:20]}")

    # Spot-check: flujos presentes en ambos para los comunes.
    comunes = sorted(m_keys & s_keys)
    sin_flujos_sql = [k for k in comunes if not (sql[k] or {}).get("flujos")
                      and (mongo[k] or {}).get("flujos")]
    if sin_flujos_sql:
        print(f"⚠ COMUNES con flujos en Mongo pero NO en SQL ({len(sin_flujos_sql)}): "
              f"{sin_flujos_sql[:20]}")

    ok = not solo_mongo and not solo_sql and not sin_flujos_sql
    print("\n✅ PARIDAD TOTAL — seguro dropear Trading.Curvas." if ok
          else "\n❌ SIN paridad — NO dropear; revisar las diferencias de arriba.")
    return 0 if ok else 1


def _s(v):
    return str(v) if v not in (None, "") else None


if __name__ == "__main__":
    sys.exit(main())
