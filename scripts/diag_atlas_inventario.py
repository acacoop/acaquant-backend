"""Diag READ-ONLY: inventario real del cluster Atlas (mapa para el rediseño de datos).

Vuelca, por base de datos: colecciones, tamaño (datos + índices), cantidad de
docs, las DEFINICIONES de cada índice y —si la credencial lo permite— su uso
REAL (`$indexStats`). Sirve para (1) ver qué hay de verdad —no del CLAUDE.md—,
(2) detectar índices muertos/redundantes antes de dropear (REGLA #2), (3) fundar
el reordenamiento de DBs/colecciones sobre datos.

100% lectura. No toca nada.

## Sobre $indexStats y los permisos
`$indexStats` (uso real de cada índice) NO lo otorga ni el rol de lectura
(MONGO_URI_READ) ni el de escritura (MONGO_URI). Lo otorga `clusterMonitor` o un
rol custom con la acción `indexStats`. Por eso da "not authorized" con esas dos
credenciales. Necesitás un usuario de MONITOREO.

`listIndexes` (las DEFINICIONES de los índices) SÍ lo permite el rol de lectura
→ con eso ya detecto índices **redundantes por prefijo** sin necesidad de usage.

Uso:
    python -m scripts.diag_atlas_inventario --list-env       # descubre qué credenciales hay (solo nombres)
    python -m scripts.diag_atlas_inventario --idx            # tamaños + definiciones de índices (read-only)
    python -m scripts.diag_atlas_inventario --idx --rw       # con MONGO_URI (rw)
    python -m scripts.diag_atlas_inventario --idx --uri-env MONGO_URI_MONITOR   # con la credencial de monitoreo
"""
from __future__ import annotations

import argparse
import os

import pymongo

from core.mongo import get_mongo_client, get_mongo_client_read

# DBs internas de Mongo que no son de la app.
_SKIP_DB = {"admin", "local", "config"}


def _fmt_bytes(n: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


def _listar_env() -> None:
    """Imprime los NOMBRES de las env vars de Mongo disponibles (nunca el valor).
    Sirve para descubrir si existe la credencial de monitoreo sin filtrar secretos.
    """
    print("== Variables de entorno relacionadas a Mongo/Atlas (solo nombres) ==")
    encontradas = []
    for k, v in sorted(os.environ.items()):
        ku = k.upper()
        if "MONGO" in ku or "ATLAS" in ku:
            # Enmascarado: solo confirma que está seteada y su longitud.
            encontradas.append((k, len(v or "")))
    if not encontradas:
        print("  (ninguna)")
    for k, ln in encontradas:
        print(f"  - {k:<28} (seteada, {ln} chars)")
    print("\nPara usar una: python -m scripts.diag_atlas_inventario --idx --uri-env <NOMBRE>")


def _key_repr(key) -> str:
    """'{a:1,b:-1}' a partir de la lista de pares de un índice."""
    if isinstance(key, dict):
        items = list(key.items())
    else:
        items = list(key)
    return "{" + ",".join(f"{k}:{v}" for k, v in items) + "}"


def _key_fields(key) -> list[str]:
    items = list(key.items()) if isinstance(key, dict) else list(key)
    return [str(k) for k, _ in items]


def _redundantes_por_prefijo(indices: list[dict]) -> list[str]:
    """Detecta índices PLANOS cuya key es prefijo de otro (cubiertos → candidatos
    a dropear). Excluye unique/partial/TTL/sparse/text de ambos lados: ahí el
    prefijo no garantiza que sobre. Determinista, no usa $indexStats.
    """
    def es_plano(ix: dict) -> bool:
        return not any(k in ix for k in
                       ("unique", "partialFilterExpression", "expireAfterSeconds",
                        "sparse", "weights"))

    nombres = []
    for a in indices:
        if a["name"] == "_id_" or not es_plano(a):
            continue
        fa = _key_fields(a["key"])
        for b in indices:
            if b is a or b["name"] == "_id_":
                continue
            fb = _key_fields(b["key"])
            if len(fb) > len(fa) and fb[:len(fa)] == fa:
                nombres.append(f"{a['name']} {_key_repr(a['key'])} ⊂ "
                               f"{b['name']} {_key_repr(b['key'])}")
                break
    return nombres


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--idx", action="store_true",
                    help="incluye definiciones de índices (+ uso real si la credencial lo permite)")
    ap.add_argument("--rw", action="store_true",
                    help="usa MONGO_URI (rw) en vez de MONGO_URI_READ")
    ap.add_argument("--uri-env", metavar="NOMBRE",
                    help="usa la URI de la env var NOMBRE (ej. credencial de monitoreo con clusterMonitor)")
    ap.add_argument("--list-env", action="store_true",
                    help="lista los nombres de env vars de Mongo disponibles y sale")
    args = ap.parse_args()

    if args.list_env:
        _listar_env()
        return 0

    if args.uri_env:
        uri = os.getenv(args.uri_env)
        if not uri:
            print(f"⚠ La env var {args.uri_env} no está seteada. Corré --list-env para ver las disponibles.")
            return 1
        cli = pymongo.MongoClient(uri, serverSelectionTimeoutMS=30000,
                                  read_preference=pymongo.ReadPreference.SECONDARY_PREFERRED)
        origen = f"--uri-env {args.uri_env}"
    elif args.rw:
        cli = get_mongo_client()
        origen = "MONGO_URI rw"
    else:
        cli = get_mongo_client_read()
        origen = "MONGO_URI_READ ro"

    print(f"(conexión: {origen})\n")
    dbs = sorted(d for d in cli.list_database_names() if d not in _SKIP_DB)
    print(f"== Cluster Atlas — {len(dbs)} bases de datos de la app ==\n")

    indexstats_ok = False
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
            if not (args.idx and nidx > 1):
                continue

            # 1) DEFINICIONES (listIndexes — lo permite el rol de lectura).
            try:
                indices = list(db[c].list_indexes())
            except Exception as e:
                print(f"        (listIndexes no disponible: {e})")
                continue

            # 2) USO real (si la credencial tiene indexStats).
            usos: dict[str, int] = {}
            try:
                usos = {d["name"]: d.get("accesses", {}).get("ops", 0)
                        for d in db[c].aggregate([{"$indexStats": {}}])}
                indexstats_ok = True
            except Exception:
                usos = {}

            for ix in indices:
                name = ix["name"]
                flags = []
                if ix.get("unique"):
                    flags.append("UNIQUE")
                if "partialFilterExpression" in ix:
                    flags.append("PARTIAL")
                if "expireAfterSeconds" in ix:
                    flags.append(f"TTL={ix['expireAfterSeconds']}s")
                if ix.get("sparse"):
                    flags.append("SPARSE")
                fl = ("  [" + ",".join(flags) + "]") if flags else ""
                if usos:
                    ops = usos.get(name, 0)
                    muerto = "  ← 0 usos (¿muerto?)" if ops == 0 and name != "_id_" else ""
                    print(f"        idx {name:<30} {_key_repr(ix['key']):<28} {ops:>9,} acc{fl}{muerto}")
                else:
                    print(f"        idx {name:<30} {_key_repr(ix['key']):<28}{fl}")

            redundantes = _redundantes_por_prefijo(indices)
            for r in redundantes:
                print(f"        ⚠ redundante por prefijo: {r}")
        print(f"  └─ subtotal: datos {_fmt_bytes(db_data)} · índices {_fmt_bytes(db_idx)}\n")
        tot_data += db_data
        tot_idx += db_idx

    print(f"== TOTAL: datos {_fmt_bytes(tot_data)} · índices {_fmt_bytes(tot_idx)} · "
          f"{len(dbs)} DBs ==")
    if args.idx and not indexstats_ok:
        print("⚠ $indexStats NO disponible con esta credencial (uso real ausente). "
              "Para el usage muerto hace falta un usuario con rol clusterMonitor "
              "(o custom con acción indexStats). Las definiciones + redundantes por "
              "prefijo de arriba SÍ son válidas.")
    elif indexstats_ok:
        print("Nota: $indexStats cuenta accesos desde el último restart del nodo — "
              "idealmente correr tras un día de uso normal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
