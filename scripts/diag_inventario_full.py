"""scripts/diag_inventario_full.py — INVENTARIO REAL de Mongo + Postgres (read-only).

Audita el ESTADO REAL de las dos bases para planear el apagado de Mongo. NO escanea
colecciones (usa contadores O(1): estimated_document_count + collStats; en PG usa
pg_stat_user_tables). Seguro de correr en horario de mercado (REGLA #4).

Por cada colección Mongo: nombre, # docs (estimado), tamaño datos+índices, última
escritura (proxy por ObjectId del _id más nuevo, índice-backed), y las top-level keys
de un doc de muestra.

Por cada tabla Postgres: schema.tabla, filas vivas (estimado), tamaño total.

Uso (en el Droplet):
    cd /root/TradingAV && git pull
    python -m scripts.diag_inventario_full            # imprime a pantalla
    python -m scripts.diag_inventario_full > /tmp/inv.txt 2>&1   # a archivo

Read-only. No modifica nada. Borrar tras usar (REGLA #5).
"""
from __future__ import annotations

import sys
import traceback

# ──────────────────────────────────────────────────────────────────────────
# MONGO
# ──────────────────────────────────────────────────────────────────────────
SKIP_DBS = {"admin", "local", "config"}


def _fmt_bytes(n: int | float | None) -> str:
    if not n:
        return "0"
    n = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}PB"


def inventario_mongo() -> None:
    from bson import ObjectId

    from core.mongo import get_mongo_client_read

    print("=" * 100)
    print("MONGO — inventario por base/colección")
    print("=" * 100)
    cli = get_mongo_client_read()
    try:
        dbs = sorted(cli.list_database_names())
    except Exception as e:
        print(f"!! No se pudieron listar las bases: {e}")
        return

    tot_docs = 0
    tot_bytes = 0
    tot_colls = 0
    for dbname in dbs:
        if dbname in SKIP_DBS:
            continue
        db = cli[dbname]
        try:
            colls = sorted(db.list_collection_names())
        except Exception as e:
            print(f"\n### DB {dbname}: error listando colecciones: {e}")
            continue
        if not colls:
            print(f"\n### DB {dbname}: (sin colecciones)")
            continue
        print(f"\n### DB {dbname}  ({len(colls)} colecciones)")
        print(f"  {'colección':<34}{'#docs':>12}  {'datos':>9}  {'índices':>9}  {'última escritura (UTC)':<26} keys(muestra)")
        print("  " + "-" * 150)
        for c in colls:
            coll = db[c]
            try:
                n = coll.estimated_document_count()
            except Exception:
                n = -1
            data_sz = idx_sz = 0
            try:
                st = db.command("collStats", c)
                data_sz = st.get("size", 0) or 0
                idx_sz = st.get("totalIndexSize", 0) or 0
            except Exception:
                pass
            # Última escritura: _id más nuevo (índice _id, O(1)); si es ObjectId
            # sacamos generation_time. Si _id es custom (str), probamos campos ts.
            last_str = "—"
            sample_keys = ""
            try:
                newest = coll.find_one(sort=[("_id", -1)])
                if newest:
                    sample_keys = ",".join(list(newest.keys())[:8])
                    _id = newest.get("_id")
                    if isinstance(_id, ObjectId):
                        last_str = _id.generation_time.strftime("%Y-%m-%d %H:%M")
                    else:
                        for f in ("updated_at", "timestamp", "ts", "fecha", "date", "_updated"):
                            if f in newest and newest[f] is not None:
                                last_str = f"{f}={str(newest[f])[:19]}"
                                break
            except Exception:
                pass
            print(f"  {c:<34}{n:>12,}  {_fmt_bytes(data_sz):>9}  {_fmt_bytes(idx_sz):>9}  {last_str:<26} {sample_keys}")
            tot_docs += max(n, 0)
            tot_bytes += data_sz + idx_sz
            tot_colls += 1
    print("\n" + "-" * 100)
    print(f"MONGO TOTAL: {tot_colls} colecciones · ~{tot_docs:,} docs · {_fmt_bytes(tot_bytes)} (datos+índices)")


# ──────────────────────────────────────────────────────────────────────────
# POSTGRES
# ──────────────────────────────────────────────────────────────────────────
def inventario_postgres() -> None:
    from core.postgres import connect

    print("\n" + "=" * 100)
    print("POSTGRES — inventario por schema/tabla (filas vivas estimadas + tamaño)")
    print("=" * 100)
    q = """
        SELECT schemaname,
               relname,
               n_live_tup,
               pg_total_relation_size(relid) AS bytes,
               COALESCE(GREATEST(last_vacuum, last_autovacuum), NULL) AS last_vac,
               COALESCE(GREATEST(last_analyze, last_autoanalyze), NULL) AS last_ana
        FROM pg_stat_user_tables
        ORDER BY schemaname, relname;
    """
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(q)
            rows = cur.fetchall()
    except Exception as e:
        print(f"!! No se pudo consultar Postgres: {e}")
        return

    if not rows:
        print("  (sin tablas con estadísticas — ¿ANALYZE nunca corrió?)")
        return

    cur_schema = None
    tot_rows = 0
    tot_bytes = 0
    print(f"  {'tabla':<40}{'filas~':>14}  {'tamaño':>10}  {'último analyze':<20}")
    for sch, tbl, nlive, by, _lv, lana in rows:
        if sch != cur_schema:
            cur_schema = sch
            print(f"\n  ── schema {sch} ──")
        ana = lana.strftime("%Y-%m-%d %H:%M") if lana else "—"
        print(f"  {tbl:<40}{(nlive or 0):>14,}  {_fmt_bytes(by):>10}  {ana:<20}")
        tot_rows += nlive or 0
        tot_bytes += by or 0
    print("\n" + "-" * 100)
    print(f"POSTGRES TOTAL: {len(rows)} tablas · ~{tot_rows:,} filas · {_fmt_bytes(tot_bytes)}")


def main() -> int:
    try:
        inventario_mongo()
    except Exception:
        print("!! Falló inventario Mongo:")
        traceback.print_exc()
    try:
        inventario_postgres()
    except Exception:
        print("!! Falló inventario Postgres:")
        traceback.print_exc()
    return 0


if __name__ == "__main__":
    sys.exit(main())
