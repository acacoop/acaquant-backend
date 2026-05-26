"""diag_index_usage.py — uso real de cada índice ($indexStats), read-only.

Complementa audit_db: por cada colección con índices propios (más allá de _id),
lista cada índice con sus `ops` (cuántas veces lo eligió el planner desde el
último restart del nodo), su tamaño y su key. Marca:
  • MUERTO  → 0 ops: candidato a dropear (confirmar que el uptime sea largo).
  • redundante → su key es prefijo de otro índice (lo cubre el compuesto).

Sirve para decidir qué índices dropear ANTES de crear nuevos (Fase 1 DBA).
No modifica nada.

    python -m scripts.diag_index_usage              # todas las colecciones con >1 índice
    python -m scripts.diag_index_usage --all        # incluye las de 1 solo índice
    python -m scripts.diag_index_usage --db Valuaciones
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client_read

_SYS = {"admin", "local", "config"}


def _h(n: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f}{u}" if u == "B" else f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}GB"


def _redundantes(keys: dict[str, tuple]) -> set[str]:
    red = set()
    for na, ka in keys.items():
        for nb, kb in keys.items():
            if na != nb and len(ka) < len(kb) and kb[: len(ka)] == ka:
                red.add(na)
                break
    return red


def main() -> None:
    ap = argparse.ArgumentParser(description="Uso de índices vía $indexStats (read-only)")
    ap.add_argument("--all", action="store_true", help="incluir colecciones con 1 solo índice")
    ap.add_argument("--db", help="acotar a una base")
    args = ap.parse_args()

    cli = get_mongo_client_read()
    dbs = [args.db] if args.db else [d for d in cli.list_database_names() if d not in _SYS]

    muertos: list[str] = []
    redund: list[str] = []

    for dbname in dbs:
        db = cli[dbname]
        for coll in sorted(c for c in db.list_collection_names() if not c.startswith("system.")):
            try:
                info = db[coll].index_information()
                if len(info) <= 1 and not args.all:
                    continue
                stats = {r["name"]: r for r in db[coll].aggregate([{"$indexStats": {}}])}
                sizes = {}
                try:
                    cs = next(db[coll].aggregate([{"$collStats": {"storageStats": {}}}]))
                    sizes = cs.get("storageStats", {}).get("indexSizes", {})
                except Exception:
                    pass
            except Exception as e:
                print(f"\n{dbname}.{coll}  ⚠ {str(e)[:50]}")
                continue

            keys = {n: tuple(s["key"]) for n, s in info.items() if n != "_id_"}
            red = _redundantes(keys)
            print(f"\n{dbname}.{coll}")
            for name in info:
                if name == "_id_":
                    continue
                ops = stats.get(name, {}).get("accesses", {}).get("ops", "?")
                sz = _h(sizes.get(name, 0))
                flags = []
                if ops == 0:
                    flags.append("MUERTO")
                    muertos.append(f"{dbname}.{coll}.{name}")
                if name in red:
                    flags.append("redundante")
                    redund.append(f"{dbname}.{coll}.{name}")
                tag = ("  ← " + ", ".join(flags)) if flags else ""
                print(f"    {name:<42} ops={ops!s:>9}  {sz:>8}{tag}")

    print("\n" + "=" * 64)
    print(f"MUERTOS (0 ops): {len(muertos)}")
    for m in muertos:
        print(f"  • {m}")
    print(f"\nREDUNDANTES: {len(redund)}")
    for r in redund:
        print(f"  • {r}")
    print("\nNota: 0 ops = no usado desde el último restart del nodo. En Atlas el")
    print("uptime suele ser largo; igual confirmá antes de dropear (un índice puede")
    print("servir a un cron mensual que todavía no corrió en esta ventana).")


if __name__ == "__main__":
    main()
