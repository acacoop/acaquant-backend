"""Diag READ-ONLY: índice TTL y antigüedad de Operaciones.OrdenesAudit.

Contexto: el índice `ts_1` tiene `expireAfterSeconds=7776000` (90 días) puesto
fuera de banda en Atlas → el audit de órdenes se auto-borra a los 90 días. Este
diag muestra los índices y el doc más viejo/nuevo, para decidir si esa retención
es deseada (la dejamos) o un error (la sacamos).

Uso:
    python -m scripts.diag_ordenes_audit_ttl
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read


def main() -> int:
    col = get_mongo_client_read()["Operaciones"]["OrdenesAudit"]

    print("== índices ==")
    for ix in col.list_indexes():
        ttl = ix.get("expireAfterSeconds")
        ttl_s = f"TTL={ttl}s (~{ttl // 86400}d)" if ttl is not None else "sin TTL"
        print(f"  {ix.get('name')}: key={dict(ix.get('key', {}))} · {ttl_s}")

    n = col.estimated_document_count()
    print(f"\ndocs (estimado): {n}")
    viejo = col.find_one({"ts": {"$exists": True}}, {"ts": 1, "_id": 0}, sort=[("ts", 1)])
    nuevo = col.find_one({"ts": {"$exists": True}}, {"ts": 1, "_id": 0}, sort=[("ts", -1)])
    print(f"audit más viejo: {viejo.get('ts') if viejo else '—'}")
    print(f"audit más nuevo: {nuevo.get('ts') if nuevo else '—'}")
    print("\nSi el más viejo es de hace <90d y esperabas tener más historia, "
          "el TTL está borrando audit (decidir si se saca).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
