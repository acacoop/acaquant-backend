"""scripts/diag_cupo_check.py — sanity del cupo en el Mongo restaurado (read-only).

Antes de re-dropear Comitentes (irreversible), confirma que el cupo de las cuentas
que SÍ lo tienen está intacto en el backup. Si "con cupo>0" ≈ las que ya están en SQL
(~1259), entonces mi lógica lee bien y las 244 en NULL genuinamente NO tienen cupo
(nada que recuperar). Si diera 0, habría un bug de lectura → NO dropear.

    python -m scripts.diag_cupo_check
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read


def main() -> int:
    db = get_mongo_client_read()["Clientes"]
    total = db["Comitentes"].count_documents({})
    con_cupo = db["Comitentes"].count_documents({"cupo.transaccional_ars": {"$ne": None}})
    con_cupo_pos = db["Comitentes"].count_documents({"cupo.transaccional_ars": {"$gt": 0}})
    print(f"Comitentes restaurada: total={total:,}")
    print(f"  con cupo.transaccional_ars != null : {con_cupo:,}")
    print(f"  con cupo.transaccional_ars > 0      : {con_cupo_pos:,}")
    print("\n3 ejemplos CON cupo (id_cuenta, cupo):")
    for d in db["Comitentes"].find(
            {"cupo.transaccional_ars": {"$gt": 0}},
            {"_id": 0, "id_cuenta": 1, "cupo.transaccional_ars": 1, "cupo.usado_ars": 1}).limit(3):
        print(f"  {d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
