"""cleanup_opciones_data_viejos.py — borra de Opciones.Data los ticks de vencimientos VIEJOS.

El diag mostró que ~98% de Opciones.Data (1.1M docs) son de opciones de vencimientos
pasados (symbols que ya NO están en OptionsSnapshot, que el motor mantiene al día). Esto
los borra, dejando solo los strikes vigentes.

SEGURO (REGLA #4): scopeado (solo symbols NO vigentes), batcheado (1 symbol por vez),
throttled (sleep entre cada uno, no starva los motores), idempotente (re-correrlo borra lo
que falte). Dry-run por default. Correr FUERA de rueda.

    python -m scripts.cleanup_opciones_data_viejos            # dry-run: qué borraría
    python -m scripts.cleanup_opciones_data_viejos --apply    # borra
"""
from __future__ import annotations

import argparse
import time

from core.mongo import get_mongo_client

_THROTTLE = 0.3   # seg entre symbols


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="borrar (default: dry-run)")
    args = ap.parse_args()

    opc = get_mongo_client()["Opciones"]
    vigentes = {d["symbol"] for d in opc["OptionsSnapshot"].find({}, {"_id": 0, "symbol": 1})
                if d.get("symbol")}
    data_symbols = [s for s in opc["Data"].distinct("symbol") if s]
    viejos = sorted(s for s in data_symbols if s not in vigentes)

    print(f"Vigentes (OptionsSnapshot): {len(vigentes)} · symbols en Data: {len(data_symbols)} · "
          f"VIEJOS a borrar: {len(viejos)}")
    if not viejos:
        print("✅ Nada para borrar (Data ya está limpio).")
        return 0

    if not args.apply:
        print("\n(DRY-RUN — nada borrado. Correr con --apply.)")
        print("OJO: correr fuera de rueda (Atlas arriba, motores parados).")
        return 0

    # Índice por symbol (best-effort) → el delete por symbol usa índice en vez de COLLSCAN.
    try:
        opc["Data"].create_index("symbol")
    except Exception as e:
        print(f"  (no se pudo crear índice symbol, sigo igual: {str(e).splitlines()[0][:60]})")

    borrados = 0
    for i, sym in enumerate(viejos, 1):
        try:
            borrados += opc["Data"].delete_many({"symbol": sym}).deleted_count
        except Exception as e:
            print(f"  ⚠ {sym}: {str(e).splitlines()[0][:80]}")
        if i % 10 == 0 or i == len(viejos):
            print(f"  {i}/{len(viejos)} symbols · {borrados:,} docs borrados")
        time.sleep(_THROTTLE)

    print(f"\n✅ {borrados:,} docs borrados de Opciones.Data. Quedan solo los strikes vigentes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
