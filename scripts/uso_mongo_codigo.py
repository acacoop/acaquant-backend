"""scripts/uso_mongo_codigo.py — mapa de USO de Mongo en el CÓDIGO (estático).

Complementa `diag_inventario_mongo_sql.py` (foto factual de prod) con lo que ese
script dice que falta: cruzar con el USO del código. Read-only, escanea el FUENTE
(no necesita prod ni Mongo) → corre en cualquier lado.

Por cada colección Mongo accedida en el repo lista:
  - en qué ARCHIVOS aparece y en qué CAPA (engine/job/service/router/mcp/core/script/test)
  - si se LEE (find/aggregate/count/distinct) y/o se ESCRIBE (insert/update/replace/
    delete/bulk_write/collMod) — detección por línea (idiom `db["X"].op(...)`)

Heurística de detección de acceso (idioms reales del repo):
  - `["DBName"]["Coll"]`           (db explícita: Trading/Valuaciones/CashFlow/…)
  - `db["Coll"]`, `cli["Coll"]`, `col(...)["Coll"]`, `get_db_xxx()["Coll"]`

VEREDICTO heurístico (se confirma a mano en el mapa):
  - sin capa api/* ni mcp        → no hay superficie de lectura viva (¿muerto?/interno)
  - api/services|routers|mcp      → lectura VIVA → migrar a SQL (si no está ya)
  - engines/jobs con escritura    → lo escribe un motor/job → necesita write SQL-native

    python -m scripts.uso_mongo_codigo            # tabla por colección
    python -m scripts.uso_mongo_codigo --files    # + el detalle archivo:línea
"""
from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DIRS = ["engines", "jobs", "api", "core", "scripts", "tests", "partner_api"]

# Bases Mongo del proyecto (CLAUDE.md) — para el idiom ["DB"]["Coll"].
_DBS = {"Trading", "Valuaciones", "CashFlow", "Clientes", "Manager", "Opciones",
        "Derivados", "Operaciones", "Market", "News", "MCP", "ACAPortfolio"}

# Acceso a colección: captura el nombre entre corchetes string.
_RE_DB_COLL = re.compile(r'\[\s*["\'](' + "|".join(_DBS) + r')["\']\s*\]\s*\[\s*["\'](\w+)["\']\s*\]')
_RE_HANDLE_COLL = re.compile(
    r'(?:\bdb|\bcli|\bclient|\bmdb|\bdatabase|get_db_\w+\(\)|get_database\([^)]*\))'
    r'\s*\[\s*["\'](\w+)["\']\s*\]')

_WRITE_OPS = re.compile(
    r'\.(insert_one|insert_many|update_one|update_many|replace_one|delete_one|'
    r'delete_many|bulk_write|find_one_and_update|find_one_and_replace|'
    r'find_one_and_delete|create_index|drop|drop_index|rename)\b|collMod')
_READ_OPS = re.compile(r'\.(find|find_one|aggregate|count_documents|'
                       r'estimated_document_count|distinct)\b')

# Nombres que NO son colecciones (db handles, falsos positivos comunes del idiom).
_NO_COLL = {"admin", "local", "config", "client", "database",
            "Coll", "DB", "DBName", "X", "redirect_uris"}

_CAPA = {
    "engines": "engine", "jobs": "job", "core": "core",
    "scripts": "script", "tests": "test", "partner_api": "partner",
}


def _capa(rel: str) -> str:
    p = rel.replace("\\", "/")
    if p.startswith("api/services"):
        return "service"
    if p.startswith("api/routers"):
        return "router"
    if p.startswith("api/mcp"):
        return "mcp"
    if p.startswith("api/"):
        return "api"
    top = p.split("/", 1)[0]
    return _CAPA.get(top, top)


def escanear() -> dict[str, dict]:
    # coll -> {capas:set, files:set, reads:bool, writes:bool, sites:list}
    data: dict[str, dict] = defaultdict(
        lambda: {"capas": set(), "files": set(), "reads": False,
                 "writes": False, "sites": []})

    for d in DIRS:
        base = RAIZ / d
        if not base.exists():
            continue
        for f in base.rglob("*.py"):
            if f.name == "uso_mongo_codigo.py":  # no auto-escanearse (ejemplos en docstring)
                continue
            rel = str(f.relative_to(RAIZ))
            capa = _capa(rel)
            try:
                lineas = f.read_text(encoding="utf-8", errors="ignore").splitlines()
            except Exception:
                continue
            for i, ln in enumerate(lineas, 1):
                colls = [m.group(2) for m in _RE_DB_COLL.finditer(ln)]
                colls += [m.group(1) for m in _RE_HANDLE_COLL.finditer(ln)]
                for c in colls:
                    if c in _NO_COLL or c in _DBS:
                        continue
                    e = data[c]
                    e["capas"].add(capa)
                    e["files"].add(rel)
                    if _WRITE_OPS.search(ln):
                        e["writes"] = True
                    if _READ_OPS.search(ln):
                        e["reads"] = True
                    e["sites"].append(f"{rel}:{i}")
    return data


def _veredicto(e: dict) -> str:
    capas = e["capas"]
    viva = capas & {"service", "router", "mcp", "api"}
    code_only = capas <= {"script", "test"}
    if not capas:
        return "-"
    if code_only:
        return "[?DEAD] solo scripts/tests"
    if viva:
        return "[MIGRAR] lectura viva -> SQL"
    if capas & {"engine", "job"}:
        return "[WRITE] motor/job -> SQL-native"
    return "revisar"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", action="store_true", help="mostrar archivo:línea")
    args = ap.parse_args()

    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    data = escanear()
    print("=" * 78)
    print("USO DE MONGO EN EL CÓDIGO (estático) — por colección")
    print("=" * 78)
    print(f"{'colección':<26}{'R':<3}{'W':<3}{'capas':<34}veredicto")
    print("-" * 78)
    for c in sorted(data, key=lambda x: (sorted(data[x]['capas']), x)):
        e = data[c]
        r = "R" if e["reads"] else "·"
        w = "W" if e["writes"] else "·"
        capas = ",".join(sorted(e["capas"]))
        print(f"{c:<26}{r:<3}{w:<3}{capas:<34}{_veredicto(e)}")
        if args.files:
            for s in sorted(set(e["sites"])):
                print(f"      {s}")

    print("-" * 78)
    vivas = [c for c, e in data.items() if e["capas"] & {"service", "router", "mcp", "api"}]
    muertas = [c for c, e in data.items() if e["capas"] <= {"script", "test"}]
    print(f"{len(data)} colecciones referenciadas | {len(vivas)} con lectura viva | "
          f"{len(muertas)} solo en scripts/tests (¿muertas?)")
    print("\nNOTA: estático y heurístico. Cruzar con diag_inventario_mongo_sql (conteos")
    print("prod) y estado_sql (flags) en docs/DECOMMISSION_MONGO.md. 'W' por línea puede")
    print("subcontar writes vía handle asignado (col = db['X']; col.update()).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
