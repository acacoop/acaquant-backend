"""scripts/delete_otc_ndf_opciones.py — purga definitiva de los boletos OTC
NDF / Opciones OTC de CashFlow.Operaciones (Mongo) Y del espejo Postgres.

Borra los `tipo_operacion` "Rueda OTC NDF OTC - Compra/Venta" y "Rueda OTC
Opciones OTC - Compra/Venta" (~107k docs, ~22% de la colección, operacion="otro",
volumen despreciable). NO toca "Concurrencia OTC". Medido con
scripts/diag_otc_ndf_opciones.py (2026-06-08).

POR QUÉ ASÍ (REGLA #4 — no escanear prod a ciegas, no tumbar el CPU):
  * `tipo_operacion` NO está indexado → borrar filtrando por él sería un COLLSCAN
    de ~491k docs en el PRIMARY (el anti-patrón que tiró el CPU al 100% 2 veces).
  * En su lugar: se leen los `_id` del SECONDARY (get_mongo_client_read, no
    starva a los motores) y se borra por `_id` (índice) en lotes + throttle.
  * IDEMPOTENTE: re-correrlo borra lo que haya quedado, sin romper nada.
  * El espejo Postgres NO se limpia solo (sync_postgres hace UPSERT, nunca DELETE)
    → hay que borrarlo acá también, o quedan 107k filas fantasma en PG.

CUÁNDO: fuera de rueda (NO 13-20 UTC L-V, cuando corren los motores).

Uso (en el Droplet):
    python -m scripts.delete_otc_ndf_opciones            # DRY-RUN (default): solo cuenta
    python -m scripts.delete_otc_ndf_opciones --apply     # ejecuta el borrado real
    python -m scripts.delete_otc_ndf_opciones --apply --solo-mongo   # solo Mongo
    python -m scripts.delete_otc_ndf_opciones --apply --solo-pg      # solo Postgres
"""
from __future__ import annotations

import argparse
import time

from core.mongo import get_mongo_client, get_mongo_client_read

# Mismo criterio que api.services.operaciones_informes.es_otc_excluido (mantener en sync).
_MATCH = {"tipo_operacion": {"$regex": "NDF\\s*OTC|Opciones\\s*OTC", "$options": "i"}}
_PG_REGEX = "NDF\\s*OTC|Opciones\\s*OTC"   # POSIX regex para Postgres (~*)

CHUNK = 2000      # _id por delete_many
THROTTLE = 0.20   # s entre lotes (no starvar al primary)


def _purga_mongo(apply: bool) -> int:
    """Lee _id del secondary, borra por _id en el primary (batcheado + throttle)."""
    rdb = get_mongo_client_read()["CashFlow"]["Operaciones"]
    print("  [mongo] leyendo _id del secondary (match por tipo_operacion)…")
    ids = [d["_id"] for d in rdb.find(_MATCH, {"_id": 1})]
    print(f"  [mongo] {len(ids):,} docs target")
    if not apply:
        print("  [mongo] DRY-RUN → no se borra nada")
        return len(ids)
    wcoll = get_mongo_client()["CashFlow"]["Operaciones"]
    borrados = 0
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i:i + CHUNK]
        res = wcoll.delete_many({"_id": {"$in": chunk}})
        borrados += res.deleted_count
        print(f"  [mongo] {borrados:,}/{len(ids):,} borrados…", end="\r")
        time.sleep(THROTTLE)
    print(f"\n  [mongo] borrados={borrados:,}")
    return borrados


def _purga_pg(apply: bool) -> int:
    """Borra del espejo operaciones (Postgres) por tipo_operacion (~* regex)."""
    try:
        from core.postgres import connect
    except Exception as e:
        print(f"  [pg] core.postgres no disponible ({e}) → salteo PG")
        return 0
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM operaciones WHERE tipo_operacion ~* %s", (_PG_REGEX,))
        n = cur.fetchone()[0]
        print(f"  [pg] {n:,} filas target")
        if not apply:
            print("  [pg] DRY-RUN → no se borra nada")
            return n
        cur.execute("DELETE FROM operaciones WHERE tipo_operacion ~* %s", (_PG_REGEX,))
        conn.commit()
        print(f"  [pg] borradas={cur.rowcount:,}")
        return cur.rowcount


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="ejecuta el borrado (sin esto: DRY-RUN)")
    ap.add_argument("--solo-mongo", action="store_true", help="solo Mongo")
    ap.add_argument("--solo-pg", action="store_true", help="solo Postgres")
    args = ap.parse_args()

    modo = "APPLY (borrado real)" if args.apply else "DRY-RUN (solo cuenta)"
    print(f"delete_otc_ndf_opciones — {modo}\n")
    if not args.solo_pg:
        _purga_mongo(args.apply)
    if not args.solo_mongo:
        _purga_pg(args.apply)
    print("\nLISTO." if args.apply else "\nDRY-RUN OK (nada se borró). Re-corré con --apply para ejecutar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
