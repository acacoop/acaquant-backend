"""cleanup_assets_lowercase.py — quita campos lowercase huérfanos de Valuaciones.Assets.

Legacy de una migración vieja lowercase → UPPERCASE. Hoy:
- La fuente de verdad son las 7 keys UPPERCASE (CARTERA, EMISOR, INSTRUMENTO,
  CLASE_ACTIVO, CALIFICACION, TICKER, VENCIMIENTO).
- Quien escribe `Valuaciones.Assets`:
    · jobs/aum.py::_sincronizar_assets_valuaciones() — UPPERCASE con $ifNull.
    · api/routers/manager/assets.py PATCH — UPPERCASE (solo).
- Las versiones lowercase son ruido: quedaron del esquema viejo, nadie las
  lee, nadie las actualiza. La función `_sincronizar_assets()` que sí escribe
  lowercase apunta a OTRA colección (TitulosAPI.AssetsAPI), no a esta.

Este script borra las 7 keys lowercase de TODOS los docs en
Valuaciones.Assets via $unset. Idempotente: re-correrlo no hace nada cuando
ya están limpios.

Riesgo: cero. Ningún consumidor lee esas keys.

Uso:
    python -m scripts.cleanup_assets_lowercase            # dry-run
    python -m scripts.cleanup_assets_lowercase --apply    # ejecuta
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client

_DB = "Valuaciones"
_COL = "Assets"

# 7 keys lowercase huérfanas — espejo de las UPPERCASE. NO incluye `unidad`
# (que es la upsert key, sí se usa) ni `CAFCI`/`actualizado_*` (no son
# duplicadas).
_LOWERCASE_KEYS = (
    "calificacion",
    "cartera",
    "clase_activo",
    "emisor",
    "ticker",
    "vencimiento",
    "instrumento",
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="ejecuta el $unset. Sin esto, DRY-RUN.")
    args = ap.parse_args()

    col = get_mongo_client()[_DB][_COL]

    # Docs que tienen AL MENOS una key lowercase huérfana (cualquiera).
    match = {"$or": [{k: {"$exists": True}} for k in _LOWERCASE_KEYS]}
    n_total = col.count_documents(match)
    n_col = col.count_documents({})

    print(f"Colección: {_DB}.{_COL}")
    print(f"N docs totales:                       {n_col:,}".replace(",", "."))
    print(f"N docs con al menos 1 key lowercase:  {n_total:,}".replace(",", "."))
    print()
    print("Keys lowercase a $unset (todas las que existan):")
    for k in _LOWERCASE_KEYS:
        n = col.count_documents({k: {"$exists": True}})
        print(f"   {k:<14} → {n:,} docs".replace(",", "."))

    if n_total == 0:
        print("\nNada para limpiar. Salgo.")
        return 0

    if not args.apply:
        print(f"\n[DRY-RUN] no se modificó nada. Re-correr con --apply.")
        return 0

    print(f"\n⚠️  Limpiando {n_total:,} docs…".replace(",", "."))
    unset_payload = {k: "" for k in _LOWERCASE_KEYS}
    res = col.update_many(match, {"$unset": unset_payload})
    print(f"✓ matched={res.matched_count:,}   modified={res.modified_count:,}"
          .replace(",", "."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
