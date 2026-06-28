"""compare_ordenes_sql_vs_mongo.py — GATE del cutover del motor de órdenes.

Read-only. Compara el espejo SQL `operaciones.*` contra Mongo `Operaciones.*` para
exigir paridad ANTES de prender `ORDENES_SQL=1` (lecturas). Correr DESPUÉS de tener
`ORDENES_SQL_WRITE=1` + baseline sync, sobre una rueda con movimiento.

NO compara el merge con el broker (ese es idéntico en ambos paths — sale live de pyRofex).
Compara el set LOCAL: OrdenesLive (cl_ord_id+status), AccountsDescubiertas (id+activa).

    python -m scripts.compare_ordenes_sql_vs_mongo
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read
from core.postgres import get_pool


def _mongo():
    return get_mongo_client_read()["Operaciones"]


def _cmp_ordenes_live() -> bool:
    m = {d["cl_ord_id"]: d.get("status") for d in
         _mongo()["OrdenesLive"].find({}, {"_id": 0, "cl_ord_id": 1, "status": 1})
         if d.get("cl_ord_id")}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT cl_ord_id, estado FROM operaciones.ordenes_live")
        s = {cid: est for cid, est in cur.fetchall()}
    solo_mongo = set(m) - set(s)
    solo_sql = set(s) - set(m)
    distinto = {k: (m[k], s[k]) for k in set(m) & set(s) if m[k] != s[k]}
    ok = not (solo_mongo or solo_sql or distinto)
    print(f"\nOrdenesLive: Mongo={len(m)} SQL={len(s)}  {'✅ PARIDAD' if ok else '⚠ DIFF'}")
    if solo_mongo:
        print(f"  solo en Mongo ({len(solo_mongo)}): {list(solo_mongo)[:10]}")
    if solo_sql:
        print(f"  solo en SQL ({len(solo_sql)}): {list(solo_sql)[:10]}")
    if distinto:
        print(f"  status distinto ({len(distinto)}): {dict(list(distinto.items())[:10])}")
    return ok


def _cmp_operativas() -> bool:
    """OperativasMep (operativa_id + status) — gate del read-side `operativa_mep_sql`
    (listado del día / drilldown). Compara el set LOCAL; el join a OrdenesLive y el
    cálculo de usd/mep efectivo son código compartido, no se re-verifican acá."""
    m = {d["operativa_id"]: d.get("status") for d in
         _mongo()["OperativasMep"].find({}, {"_id": 0, "operativa_id": 1, "status": 1})
         if d.get("operativa_id")}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, data->>'status' FROM operaciones.operativas_mep")
        s = {oid: st for oid, st in cur.fetchall()}
    solo_mongo = set(m) - set(s)
    solo_sql = set(s) - set(m)
    distinto = {k: (m[k], s[k]) for k in set(m) & set(s) if m[k] != s[k]}
    ok = not (solo_mongo or solo_sql or distinto)
    print(f"\nOperativasMep: Mongo={len(m)} SQL={len(s)}  {'✅ PARIDAD' if ok else '⚠ DIFF'}")
    if solo_mongo:
        print(f"  solo en Mongo ({len(solo_mongo)}): {list(solo_mongo)[:10]}")
    if solo_sql:
        print(f"  solo en SQL ({len(solo_sql)}): {list(solo_sql)[:10]}")
    if distinto:
        print(f"  status distinto ({len(distinto)}): {dict(list(distinto.items())[:10])}")
    return ok


def _cmp_accounts() -> bool:
    m = {str(d["account_id"]): bool(d.get("activa")) for d in
         _mongo()["AccountsDescubiertas"].find({}, {"_id": 0, "account_id": 1, "activa": 1})
         if d.get("account_id")}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT account_id, activa FROM operaciones.accounts_descubiertas")
        s = {str(a): bool(act) for a, act in cur.fetchall()}
    solo_mongo = set(m) - set(s)
    solo_sql = set(s) - set(m)
    distinto = {k: (m[k], s[k]) for k in set(m) & set(s) if m[k] != s[k]}
    ok = not (solo_mongo or solo_sql or distinto)
    print(f"\nAccountsDescubiertas: Mongo={len(m)} SQL={len(s)}  {'✅ PARIDAD' if ok else '⚠ DIFF'}")
    if solo_mongo:
        print(f"  solo en Mongo ({len(solo_mongo)}): {list(solo_mongo)[:10]}")
    if solo_sql:
        print(f"  solo en SQL ({len(solo_sql)}): {list(solo_sql)[:10]}")
    if distinto:
        print(f"  activa distinta ({len(distinto)}): {dict(list(distinto.items())[:10])}")
    return ok


def main() -> int:
    print("GATE motor de órdenes — paridad SQL ↔ Mongo (set LOCAL, sin merge broker)")
    ok = all([_cmp_ordenes_live(), _cmp_operativas(), _cmp_accounts()])
    print("\n" + ("✅ PARIDAD TOTAL — listo para ORDENES_SQL=1" if ok
                  else "⚠ HAY DIFERENCIAS — NO flipear lecturas hasta resolver (revisar arriba)"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
