"""diag_carteras_sql_vs_mongo.py — READ-ONLY. Por qué "no aparecen TODAS las carteras".

La lista de carteras (/valuaciones → selector) = portfolio.listar_cuentas. Con
PORTFOLIO_SQL=1 sale de la tabla `aum` de Postgres (cuentas del último snapshot que
tenga SQL); si no, de Mongo. Si SQL está incompleto o atrasado vs Mongo, la lista sale
CORTA — para todos los usuarios (en este endpoint NO hay filtro por rol; lo de
"asistentes ven menos" probablemente es solo quién lo reportó).

Compara, para el último snapshot, las cuentas distintas en Mongo (Valuaciones.AuM)
vs SQL (aum) y reporta el diff + los flags de engine.

READ-ONLY. distinct sobre el último snapshot (índice fecha_snapshot) → barato.

Uso:
    python -m scripts.diag_carteras_sql_vs_mongo
"""
from __future__ import annotations

import os

from core.mongo import get_mongo_client_read


def main() -> None:
    print("=== Diag carteras: SQL vs Mongo ===")
    print(f"    AUTH_SQL={os.getenv('AUTH_SQL', '0')!r}  PORTFOLIO_SQL={os.getenv('PORTFOLIO_SQL', '0')!r}")
    engine = "SQL" if os.getenv("PORTFOLIO_SQL") == "1" else "Mongo"
    print(f"    → la lista de carteras la sirve: {engine}\n")

    # ── Mongo (fuente de verdad) ───────────────────────────────────────────────
    aum = get_mongo_client_read()["Valuaciones"]["AuM"]
    snap = aum.find_one({}, {"_id": 0, "fecha_snapshot": 1}, sort=[("fecha_snapshot", -1)])
    mongo_snap = snap["fecha_snapshot"] if snap else None
    mongo_ids: set[str] = set()
    if mongo_snap:
        mongo_ids = {str(x) for x in aum.distinct("id_cuenta", {"fecha_snapshot": mongo_snap})}
    print(f"[Mongo]  último snapshot={mongo_snap}  cuentas distintas={len(mongo_ids)}")

    # ── SQL (lo que ve la app si PORTFOLIO_SQL=1) ──────────────────────────────
    sql_snap = None
    sql_ids: set[str] = set()
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT max(fecha_snapshot) FROM aum")
            row = cur.fetchone()
            sql_snap = row[0] if row else None
            if sql_snap is not None:
                cur.execute("SELECT DISTINCT id_cuenta FROM aum WHERE fecha_snapshot = %s", (sql_snap,))
                sql_ids = {str(r[0]) for r in cur.fetchall()}
        print(f"[SQL]    último snapshot={sql_snap}  cuentas distintas={len(sql_ids)}")
    except Exception as e:
        print(f"[SQL]    no se pudo consultar Postgres: {type(e).__name__}: {e}")
        return
    print()

    # ── Diagnóstico ────────────────────────────────────────────────────────────
    print("── Diagnóstico ──\n")
    if str(sql_snap) != str(mongo_snap):
        print(f"    🔴 SQL está ATRASADO: su último snapshot ({sql_snap}) no es el de Mongo ({mongo_snap}).")
        print("       → el sync de AuM no trajo el snapshot de hoy. La lista sale del día viejo.\n")
    falt_sql = mongo_ids - sql_ids
    if falt_sql:
        print(f"    🔴 SQL tiene MENOS cuentas: faltan {len(falt_sql)} de {len(mongo_ids)} "
              f"({len(falt_sql) / max(len(mongo_ids), 1) * 100:.1f}%).")
        print(f"       ejemplos que NO están en SQL: {sorted(falt_sql)[:15]}")
        print("       → si PORTFOLIO_SQL=1, estas son las carteras que 'no aparecen'.\n")
    if not falt_sql and str(sql_snap) == str(mongo_snap):
        print("    ✅ SQL y Mongo coinciden en cuentas y snapshot.")
        print("       → la lista NO sale corta por SQL. Si igual faltan, mirar el front/role o")
        print("         confirmar con qué usuario y qué carteras puntuales no ve.\n")

    print("=== Lectura ===")
    print("  - PORTFOLIO_SQL=1 + SQL incompleto/atrasado → lista de carteras corta (causa del sync).")
    print("  - Fix inmediato: forzar el sync (python -m jobs.sync_postgres) y revisar la ventana de AuM.")
    print("  - PORTFOLIO_SQL≠1 → la lista sale de Mongo (completa); el problema sería otro (front/role).")


if __name__ == "__main__":
    main()
