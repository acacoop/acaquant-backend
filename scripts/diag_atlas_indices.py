"""scripts/diag_atlas_indices.py — qué índices DROPEAR y CREAR, según el propio Atlas.

Usa el Performance Advisor de la Atlas Admin API (key read-only `ATLAS_RO_*`, la
misma del watchdog) — NO el motor Mongo. Por eso no necesita el privilegio
`indexStats` (que ni el user de lectura ni el rw tienen): es Atlas quien ya
computó qué índices están ocultos/redundantes/sin uso y cuáles faltan.

Lo que devuelve es PURAMENTE ESTRUCTURAL (namespace + campos del índice), sin
valores financieros → se imprime tal cual (a diferencia de las slow queries, que
core.atlas_api redacta). 100% lectura, no toca nada.

Requiere en el .env: ATLAS_RO_PUBLIC_KEY/ATLAS_RO_PRIVATE_KEY (o ATLAS_PUBLIC_KEY/
ATLAS_PRIVATE_KEY), ATLAS_PROJECT_ID y ATLAS_CLUSTER_NAME. La key necesita el
permiso 'Project Data Access Read Only' para el Performance Advisor.

Uso:
    python -m scripts.diag_atlas_indices
    python -m scripts.diag_atlas_indices --raw   # vuelca el JSON crudo (por si cambia el schema)
"""
from __future__ import annotations

import argparse
import json

from dotenv import load_dotenv

from core import atlas_api

load_dotenv()  # ATLAS_* / ATLAS_RO_* del .env (mismo patrón que el watchdog)


def _key_repr(weights) -> str:
    """Representa la key de un índice. Atlas la manda como lista de {campo:dir}
    o como dict — normalizamos a '{a:1,b:-1}'."""
    pares = []
    if isinstance(weights, dict):
        pares = list(weights.items())
    elif isinstance(weights, list):
        for w in weights:
            if isinstance(w, dict):
                pares.extend(w.items())
    return "{" + ",".join(f"{k}:{v}" for k, v in pares) + "}"


def _print_drop(data: dict) -> int:
    """Imprime las sugerencias de DROP. Tolerante al schema: prueba las claves
    conocidas (hiddenIndexes, redundantIndexes) y cae a un dump si no matchea."""
    total = 0
    for clave, etiqueta in (("hiddenIndexes", "OCULTO"),
                            ("redundantIndexes", "REDUNDANTE"),
                            ("unusedIndexes", "SIN USO")):
        items = data.get(clave) or []
        if not items:
            continue
        print(f"\n  ── {etiqueta} ({len(items)}) ──")
        for ix in items:
            ns = ix.get("namespace") or ix.get("ns") or "?"
            name = ix.get("name") or ix.get("indexName") or "?"
            key = _key_repr(ix.get("index") or ix.get("weights") or ix.get("key"))
            access = ix.get("accessCount")
            acc = f"  ({access:,} accesos)" if isinstance(access, int) else ""
            print(f"    {ns:40} {name:30} {key}{acc}")
            total += 1
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", action="store_true", help="vuelca el JSON crudo de cada endpoint")
    args = ap.parse_args()

    # ── DROP: índices a eliminar (ocultos / redundantes / sin uso) — cluster-scoped ──
    print("══ Performance Advisor — índices a DROPEAR (cluster) ══")
    try:
        drop = atlas_api.drop_index_suggestions()
        if args.raw:
            print(json.dumps(drop, indent=2, default=str))
        n = _print_drop(drop)
        if n == 0:
            print("  (Atlas no sugiere dropear ningún índice — o falta el permiso "
                  "'Data Access Read Only'. Probá --raw para ver la respuesta.)")
    except Exception as e:
        print(f"  ⚠ no disponible: {str(e)[:200]}")
        print("  (revisá ATLAS_CLUSTER_NAME y que la key tenga 'Data Access Read Only')")

    # ── CREATE: índices que Atlas sugiere agregar, por nodo — process-scoped ──
    print("\n══ Performance Advisor — índices SUGERIDOS para crear (por nodo) ══")
    try:
        nodos = atlas_api.processes()
    except Exception as e:
        print(f"  ⚠ no se pudo listar nodos: {str(e)[:160]}")
        return 0

    for n in nodos:
        pid = n.get("id")
        alias = (n.get("userAlias") or pid or "?").split(".")[0]
        if not pid:
            continue
        try:
            sug = atlas_api.suggested_indexes(pid)
        except Exception as e:
            print(f"  {alias}: sin acceso ({str(e)[:80]})")
            continue
        if args.raw:
            print(f"  [{alias}] {json.dumps(sug, indent=2, default=str)}")
        shapes = sug.get("suggestedIndexes") or []
        if not shapes:
            continue
        print(f"  ── {alias} ({len(shapes)}) ──")
        for s in shapes:
            ns = s.get("namespace") or "?"
            key = _key_repr(s.get("index") or s.get("weights"))
            impact = s.get("impact") or s.get("avgObjSize") or ""
            print(f"    {ns:40} {key}  {impact}")

    print("\nNota: el Performance Advisor analiza la actividad reciente del cluster — "
          "lo que ves es el veredicto de Atlas, no una heurística nuestra.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
