"""scripts/diag_cedears_universo.py — drift del universo CEDEARs Mongo↔SQL (read-only).

El MOTOR (engines/motor_cedears.py) se suscribe leyendo Mongo `Trading.Cedears` con
activo=True. El EDITOR (Manager → Renta Variable) y el scanner leen/escriben SQL
`mercado.cedears` (rubro/es_ia/activo). Si driftean, hay tickers clasificados en SQL
que el motor (Mongo) NO suscribe → "faltan tickers".

Mide el gap exacto: qué hay en cada lado y, sobre todo, qué está clasificado/activo en
SQL pero el motor NO trackea. NO escribe nada.

    python -m scripts.diag_cedears_universo
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read
from core.postgres import get_pool


def main() -> int:
    # Mongo Trading.Cedears (lo que el motor usa de universo).
    mdb = get_mongo_client_read()["Trading"]
    mongo_docs = list(mdb["Cedears"].find({}, {"_id": 0, "ticker": 1, "activo": 1}))
    mongo_total = len(mongo_docs)
    mongo_activo = {d["ticker"] for d in mongo_docs if d.get("activo") is True}

    # SQL mercado.cedears (editor + scanner).
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, activo, rubro FROM mercado.cedears")
        sql_rows = cur.fetchall()
    sql_total = len(sql_rows)
    sql_activo = {t for t, activo, _ in sql_rows if activo is True}
    sql_clasif = {t for t, _, rubro in sql_rows if rubro}
    sql_all = {t for t, _, _ in sql_rows}

    print("UNIVERSO CEDEARs — Mongo (motor) vs SQL (editor/scanner)\n")
    print(f"  Mongo Trading.Cedears : total={mongo_total:<4} activo=True={len(mongo_activo)}")
    print(f"  SQL   mercado.cedears : total={sql_total:<4} activo=True={len(sql_activo)} "
          f"con_rubro={len(sql_clasif)}")

    # EL BUG: clasificados/activos en SQL que el motor (Mongo activo=True) NO suscribe.
    clasif_no_motor = sorted(sql_clasif - mongo_activo)
    sqlactivo_no_motor = sorted(sql_activo - mongo_activo)
    motor_no_sql = sorted(mongo_activo - sql_all)

    print(f"\n  ⚠ Clasificados en SQL que el motor NO suscribe: {len(clasif_no_motor)}")
    print(f"    {clasif_no_motor[:40]}")
    print(f"\n  ⚠ activo=True en SQL que el motor NO suscribe: {len(sqlactivo_no_motor)}")
    print(f"    {sqlactivo_no_motor[:40]}")
    print(f"\n  · En motor (Mongo activo) pero NO en SQL: {len(motor_no_sql)}")
    print(f"    {motor_no_sql[:40]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
