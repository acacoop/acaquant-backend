"""scripts/backfill_cupo_sql.py — recupera el CUPO Mongo→SQL (agujero de migración).

`clientes.comitentes.cupo_transaccional_ars` quedó NULL en SQL para ~170 cuentas activas
cuyo cupo vivía en el subdoc `cupo` de Mongo `Clientes.Comitentes` y NO migró. Eso rompe
la segmentación (segmentar_patrimonial las pondría en None) y cualquier vista que lea el
cupo de SQL. Este backfill lo copia de Mongo a las columnas SQL.

Scopeado SOLO a las activas con cupo NULL en SQL (REGLA #4), idempotente (UPDATE por id),
read-only sobre Mongo. Dry-run por default: reporta cuánto es recuperable antes de escribir.

    python -m scripts.backfill_cupo_sql            # dry-run (no escribe)
    python -m scripts.backfill_cupo_sql --apply    # copia cupo Mongo→SQL

Borrar del repo cuando Clientes.Comitentes (Mongo) se dropee. REGLA #0/#4/#5.
"""
from __future__ import annotations

import sys

from core.mongo import get_mongo_client_read
from core.postgres import get_pool


def main() -> int:
    apply = "--apply" in sys.argv

    # 1. SQL: cuentas activas con cupo_transaccional_ars NULL.
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id_cuenta FROM comitentes "
                    "WHERE estado = 'Activa' AND cupo_transaccional_ars IS NULL")
        ids = [str(r[0]) for r in cur.fetchall()]
    print(f"Activas con cupo NULL en SQL: {len(ids)}")
    if not ids:
        print("Nada que backfillear.")
        return 0

    # 2. Mongo: leer el subdoc cupo de esas cuentas (id_cuenta string).
    db = get_mongo_client_read()["Clientes"]
    try:
        n_mongo = db["Comitentes"].estimated_document_count()
    except Exception:
        n_mongo = -1
    print(f"Mongo Clientes.Comitentes: ~{n_mongo:,} docs")
    if n_mongo == 0:
        print("⚠ Clientes.Comitentes VACÍA/dropeada en Mongo → el cupo NO es recuperable de acá.")
        return 0

    docs = list(db["Comitentes"].find(
        {"id_cuenta": {"$in": ids}}, {"_id": 0, "id_cuenta": 1, "cupo": 1}))
    print(f"Matcheadas en Mongo (por id_cuenta string): {len(docs)}")

    updates: list[tuple] = []
    sin_cupo = 0
    for d in docs:
        cupo = d.get("cupo") or {}
        t = cupo.get("transaccional_ars")
        u = cupo.get("usado_ars")
        if t is None and u is None:
            sin_cupo += 1
            continue
        updates.append((t, u, str(d["id_cuenta"])))

    print(f"Con cupo recuperable: {len(updates)}  |  matcheadas sin cupo: {sin_cupo}  |  "
          f"no matcheadas en Mongo: {len(ids) - len(docs)}")

    if not apply:
        print("\nPrimeras 15 (transaccional_ars, usado_ars, id_cuenta):")
        for r in updates[:15]:
            print(f"  {r}")
        print("\nDRY-RUN. Nada se escribió. Re-correr con --apply.")
        return 0

    if not updates:
        print("Sin cupo recuperable → nada que escribir.")
        return 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "UPDATE comitentes SET cupo_transaccional_ars = %s, cupo_usado_ars = %s "
            "WHERE id_cuenta = %s", updates)
        conn.commit()
    print(f"\n✅ {len(updates)} cupos copiados Mongo→SQL.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
