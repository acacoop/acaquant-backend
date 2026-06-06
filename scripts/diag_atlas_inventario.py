"""Diag READ-ONLY: inventario real del cluster Atlas (mapa para el rediseño de datos).

Vuelca, por base de datos: colecciones, tamaño (datos + índices), cantidad de
docs (estimada), y por cada índice su uso REAL (`$indexStats` → cuántas veces
se accedió). Sirve para (1) ver qué hay de verdad —no del CLAUDE.md—, (2)
detectar índices muertos antes de dropear (REGLA #2), (3) fundar el reordenamiento
de DBs/colecciones sobre datos.

100% lectura. No toca nada. Si podés, corrélo fuera de rueda (lee metadata, es
barato, pero $indexStats agrega un poco).

Uso:
    python -m scripts.diag_atlas_inventario
    python -m scripts.diag_atlas_inventario --idx          # uso de cada índice
    python -m scripts.diag_atlas_inventario --idx --rw     # con credencial rw (MONGO_URI)

`$indexStats` necesita un privilegio que el usuario read-only (MONGO_URI_READ)
NO tiene → con --rw usa la "llave maestra" MONGO_URI (la de motores/crons), que
sí lo tiene. Sigue siendo 100% lectura: solo corre collStats + $indexStats.
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client, get_mongo_client_read

# DBs internas de Mongo que no son de la app.
_SKIP_DB = {"admin", "local", "config"}


def _fmt_bytes(n: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--idx", action="store_true", help="incluye uso real de cada índice ($indexStats)")
    ap.add_argument("--rw", action="store_true",
                    help="usa MONGO_URI (rw) en vez de MONGO_URI_READ — necesario para $indexStats")
    args = ap.parse_args()

    cli = get_mongo_client() if args.rw else get_mongo_client_read()
    print(f"(conexión: {'MONGO_URI rw' if args.rw else 'MONGO_URI_READ ro'})\n")
    dbs = sorted(d for d in cli.list_database_names() if d not in _SKIP_DB)
    print(f"== Cluster Atlas — {len(dbs)} bases de datos de la app ==\n")

    tot_data = tot_idx = 0
    for dbn in dbs:
        db = cli[dbn]
        colls = sorted(db.list_collection_names())
        print(f"### {dbn}  ({len(colls)} colecciones)")
        db_data = db_idx = 0
        for c in colls:
            try:
                st = db.command("collStats", c)
            except Exception as e:
                print(f"  - {c}: (no se pudo leer: {e})")
                continue
            n = st.get("count", 0)
            data_sz = st.get("size", 0)
            idx_sz = st.get("totalIndexSize", 0)
            nidx = st.get("nindexes", 0)
            db_data += data_sz
            db_idx += idx_sz
            ts = " [TS]" if st.get("timeseries") else ""
            print(f"  - {c:<26} {n:>9,} docs · datos {_fmt_bytes(data_sz):>8} · "
                  f"{nidx} idx ({_fmt_bytes(idx_sz)}){ts}")
            if args.idx and nidx > 1:
                try:
                    usos = {d["name"]: d.get("accesses", {}).get("ops", 0)
                            for d in db[c].aggregate([{"$indexStats": {}}])}
                    muertos = [k for k, v in usos.items() if v == 0 and k != "_id_"]
                    for name, ops in sorted(usos.items(), key=lambda x: x[1]):
                        flag = "  ← 0 usos (¿muerto?)" if ops == 0 and name != "_id_" else ""
                        print(f"        idx {name:<34} {ops:>10,} accesos{flag}")
                    if muertos:
                        print(f"        ⚠ índices sin uso: {muertos}")
                except Exception as e:
                    print(f"        ($indexStats no disponible: {e})")
        print(f"  └─ subtotal: datos {_fmt_bytes(db_data)} · índices {_fmt_bytes(db_idx)}\n")
        tot_data += db_data
        tot_idx += db_idx

    print(f"== TOTAL: datos {_fmt_bytes(tot_data)} · índices {_fmt_bytes(tot_idx)} · "
          f"{len(dbs)} DBs ==")
    print("Nota: $indexStats cuenta accesos desde el último restart del nodo — "
          "correr con --idx idealmente tras un día de uso normal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
