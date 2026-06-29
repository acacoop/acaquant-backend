"""scripts/diag_partner_sql.py — paridad ACAPortfolio (Mongo) ↔ partner.* (SQL). Read-only.

Confirma que el baseline (scripts/partner_sql_baseline) sembró partner.cartera/api_users y
que matchean la base Mongo del proveedor — ANTES de prender PARTNER_SQL / dropear ACAPortfolio.
No necesita auth ni la pass del proveedor. NO escribe nada.

    python -m scripts.diag_partner_sql
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read
from partner_api import pg


def main() -> int:
    mdb = get_mongo_client_read()["ACAPortfolio"]
    m_cart = mdb["Cartera"].estimated_document_count()
    m_users = mdb["ApiUsers"].estimated_document_count()
    mc = mdb["Cartera"].find_one(sort=[("fecha", -1)], projection={"_id": 0, "fecha": 1})
    m_fecha = (mc or {}).get("fecha")

    with pg.get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM partner.cartera")
        s_cart = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM partner.api_users")
        s_users = cur.fetchone()[0]
        cur.execute("SELECT max(fecha) FROM partner.cartera")
        s_fecha = cur.fetchone()[0]

    ok_c = m_cart == s_cart
    ok_u = m_users == s_users
    print("PARIDAD  ACAPortfolio (Mongo) ↔ partner.* (SQL)\n")
    print(f"  Cartera    Mongo={m_cart:>6,}   SQL={s_cart:>6,}   {'✅' if ok_c else '⚠ DIFF'}")
    print(f"  ApiUsers   Mongo={m_users:>6}   SQL={s_users:>6}   {'✅' if ok_u else '⚠ DIFF'}")
    print(f"  max(fecha) Mongo={m_fecha}   SQL={s_fecha}")
    print()
    if ok_c and ok_u and s_cart > 0:
        print("✅ Paridad OK → seguro prender PARTNER_SQL=1 y, tras verificar el proveedor, dropear ACAPortfolio.")
    elif s_cart == 0:
        print("⚠ partner.cartera VACÍA → corré primero `python -m scripts.partner_sql_baseline`.")
    else:
        print("⚠ Hay diferencias — revisar antes de prender PARTNER_SQL / dropear.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
