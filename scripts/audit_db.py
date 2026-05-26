"""audit_db.py — auditoría DBA del cluster Mongo (read-only).

Recorre TODAS las bases/colecciones del cluster y produce un reporte accionable:
inventario (tamaño datos/índices, #docs), salud de índices (muertos / redundantes),
retención (TTL) y, opcional, consistencia de schema por muestreo.

Read-only puro (get_mongo_client_read). No modifica nada — solo diagnostica y
sugiere. Herramienta reusable: correr cada tanto para mantener la DB ordenada.

    python -m scripts.audit_db              # inventario + índices + TTL
    python -m scripts.audit_db --schema     # + muestreo de tipos por campo (más lento)
    python -m scripts.audit_db --db Trading # acotar a una base

FINDINGS al final, priorizados. Nada se borra solo: el reporte sugiere, vos decidís.
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from core.mongo import get_mongo_client_read

_SYS_DBS = {"admin", "local", "config"}


def _h(n: float) -> str:
    """Bytes → humano."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def _collstats(db, coll: str) -> dict:
    try:
        r = next(db[coll].aggregate([{"$collStats": {"storageStats": {}}}]))
        s = r.get("storageStats", {})
        return {
            "count": s.get("count", 0),
            "size": s.get("size", 0),
            "storage": s.get("storageSize", 0),
            "idx_size": s.get("totalIndexSize", 0),
            "idx_sizes": s.get("indexSizes", {}),
            "avg": s.get("avgObjSize", 0),
        }
    except Exception as e:
        return {"error": str(e)[:60], "count": 0, "size": 0, "storage": 0,
                "idx_size": 0, "idx_sizes": {}, "avg": 0}


def _index_usage(db, coll: str) -> dict[str, int]:
    """{nombre_idx: ops} desde $indexStats. ops=0 → nunca usado desde el último
    restart del nodo (en Atlas el uptime suele ser largo → señal confiable)."""
    out: dict[str, int] = {}
    try:
        for r in db[coll].aggregate([{"$indexStats": {}}]):
            out[r["name"]] = r.get("accesses", {}).get("ops", 0)
    except Exception:
        pass
    return out


def _redundantes(indexes: dict) -> list[tuple[str, str]]:
    """Índices cuya key es prefijo EXACTO (mismo orden) de otra → el corto es
    redundante (el largo lo cubre). Devuelve [(corto, largo)]."""
    keys = {name: tuple(spec["key"]) for name, spec in indexes.items() if name != "_id_"}
    red = []
    for na, ka in keys.items():
        for nb, kb in keys.items():
            if na != nb and len(ka) < len(kb) and kb[: len(ka)] == ka:
                red.append((na, nb))
                break
    return red


def _ttl(indexes: dict) -> bool:
    return any("expireAfterSeconds" in spec for spec in indexes.values())


def _schema_sample(db, coll: str, n: int = 300) -> list[str]:
    """Muestreo: detecta campos top-level con tipos inconsistentes y % presencia."""
    tipos: dict[str, set] = defaultdict(set)
    presente: dict[str, int] = defaultdict(int)
    total = 0
    try:
        for d in db[coll].aggregate([{"$sample": {"size": n}}]):
            total += 1
            for k, v in d.items():
                if k == "_id":
                    continue
                tipos[k].add(type(v).__name__)
                presente[k] += 1
    except Exception:
        return []
    if not total:
        return []
    notas = []
    for k, ts in tipos.items():
        if len(ts) > 1:
            notas.append(f"campo '{k}' con tipos mixtos {sorted(ts)}")
    return notas


def main() -> None:
    ap = argparse.ArgumentParser(description="Auditoría DBA del cluster Mongo (read-only)")
    ap.add_argument("--schema", action="store_true", help="muestrear tipos por campo (más lento)")
    ap.add_argument("--db", help="acotar a una base")
    args = ap.parse_args()

    cli = get_mongo_client_read()
    dbs = [args.db] if args.db else [d for d in cli.list_database_names() if d not in _SYS_DBS]

    findings: list[str] = []

    for dbname in dbs:
        db = cli[dbname]
        # Saltear colecciones de sistema (system.views/buckets/profile): no
        # permiten listIndexes y son internas de time-series / vistas.
        colls = sorted(c for c in db.list_collection_names() if not c.startswith("system."))
        if not colls:
            continue
        print("\n" + "█" * 84)
        print(f"  DB: {dbname}  ({len(colls)} colecciones)")
        print("█" * 84)
        print(f"{'colección':<30}{'#docs':>11}{'datos':>10}{'índices':>10}{'#idx':>6}{'avg':>8}  TTL")
        print("-" * 84)
        for coll in colls:
            try:
                st = _collstats(db, coll)
                idxs = db[coll].index_information()
                usage = _index_usage(db, coll)
            except Exception as e:
                print(f"{coll:<30}  ⚠ no auditable: {str(e)[:40]}")
                continue
            ttl = _ttl(idxs)
            n_idx = len(idxs)
            print(f"{coll:<30}{st['count']:>11,}{_h(st['size']):>10}{_h(st['idx_size']):>10}"
                  f"{n_idx:>6}{_h(st['avg']):>8}  {'sí' if ttl else '—'}")

            # Findings por colección
            big = st["count"] >= 50_000
            # 1. índices muertos
            muertos = [n for n, ops in usage.items() if ops == 0 and n != "_id_"]
            if muertos:
                findings.append(f"[{dbname}.{coll}] índices SIN USO (0 ops): {', '.join(muertos)}")
            # 2. redundantes
            for corto, largo in _redundantes(idxs):
                findings.append(f"[{dbname}.{coll}] índice redundante '{corto}' (cubierto por '{largo}')")
            # 3. grande sin índices propios
            if big and n_idx <= 1:
                findings.append(f"[{dbname}.{coll}] {st['count']:,} docs y SIN índices (solo _id) → queries = COLLSCAN")
            # 4. grande sin TTL
            if big and not ttl:
                findings.append(f"[{dbname}.{coll}] {st['count']:,} docs sin TTL → ¿crece sin límite? evaluar retención")
            # 5. más índice que datos
            if st["size"] > 0 and st["idx_size"] > st["size"] * 1.5 and n_idx > 2:
                findings.append(f"[{dbname}.{coll}] índices ({_h(st['idx_size'])}) > 1.5× datos ({_h(st['size'])}) → posible sobre-indexación")
            # 6. schema
            if args.schema:
                for nota in _schema_sample(db, coll):
                    findings.append(f"[{dbname}.{coll}] {nota}")

    print("\n" + "=" * 84)
    print(f"  FINDINGS ({len(findings)})")
    print("=" * 84)
    if not findings:
        print("  Sin hallazgos automáticos. (Igual conviene revisar el inventario a ojo.)")
    for f in findings:
        print(f"  • {f}")
    print("\nNota: 'índices sin uso' = 0 ops desde el último restart del nodo; en Atlas")
    print("el uptime suele ser largo, pero confirmá antes de dropear un índice.")


if __name__ == "__main__":
    main()
