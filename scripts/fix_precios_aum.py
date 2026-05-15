"""fix_precios_aum.py — corrige precios mal valuados en Valuaciones.AuM.

Los precios del snapshot 2025-08-31 vinieron mal de Aunesa. Este script
los reemplaza con los precios correctos de `docs/precios_3108.json` — un
map {unidad: precio_ARS} derivado del Excel que pasó el usuario — y
RECALCULA la valuación de cada posición con la MISMA lógica que el job
(jobs/aum.py::_calcular_valuacion: /100 para renta fija — Títulos,
Letras, ONs, FF, CPD —, precio+1 para futuros).

El precio de un instrumento es global, así que se aplica a TODAS las
cuentas del snapshot. Match por `unidad`. El update apunta a cada doc por
su `_id` (cero ambigüedad).

Las unidades del snapshot que NO están en el doc no se tocan — se listan.

Dry-run por default. Pasa --apply para escribir.

Corre:  python -m scripts.fix_precios_aum [--snapshot 2025-08-31] [--apply]
"""
from __future__ import annotations

import argparse
import json
import os

from pymongo import UpdateOne

from core.mongo import get_mongo_client
from jobs.aum import _calcular_valuacion

_SNAPSHOT_DEFAULT = "2025-08-31"
_PRECIOS_DEFAULT = "docs/precios_3108.json"
_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", default=_SNAPSHOT_DEFAULT,
                    help=f"fecha_snapshot a corregir (default {_SNAPSHOT_DEFAULT})")
    ap.add_argument("--precios", default=_PRECIOS_DEFAULT,
                    help=f"JSON de precios {{unidad: precio}}, ruta desde la "
                         f"raíz del repo (default {_PRECIOS_DEFAULT})")
    ap.add_argument("--cuenta",
                    help="CSV de id_cuenta — corrige solo esas cuentas del "
                         "snapshot (default: todas).")
    ap.add_argument("--apply", action="store_true", help="escribe los cambios")
    args = ap.parse_args()

    precios_path = os.path.join(_REPO_ROOT, args.precios)
    with open(precios_path, encoding="utf-8") as f:
        precios: dict[str, float] = json.load(f)

    print(f"Precios cargados: {len(precios)} unidades ({args.precios})")
    print(f"Snapshot:         {args.snapshot}")
    print(f"Modo:             {'APPLY (escribe)' if args.apply else 'DRY-RUN'}")
    print("=" * 88)

    col = get_mongo_client()["Valuaciones"]["AuM"]
    match: dict = {"fecha_snapshot": args.snapshot}
    if args.cuenta:
        ids = [c.strip() for c in args.cuenta.split(",") if c.strip()]
        match["id_cuenta"] = {"$in": ids}
        print(f"Cuentas:          {ids}")
    docs = list(col.find(match))
    print(f"Docs de AuM en el snapshot: {len(docs)}")

    cambios: list[dict] = []
    sin_precio: set[str] = set()
    for d in docs:
        unidad = d.get("unidad")
        if unidad not in precios:
            sin_precio.add(str(unidad))
            continue
        precio_nuevo = float(precios[unidad])
        precio_viejo = float(d.get("precio") or 0)
        val_vieja = float(d.get("valuacion") or 0)
        # Recalcular valuacion con la logica EXACTA del job.
        val_nueva = float(_calcular_valuacion({
            "precio":     precio_nuevo,
            "cantidad":   d.get("cantidad"),
            "tipoTitulo": d.get("tipoTitulo"),
        }))
        if (abs(precio_nuevo - precio_viejo) < 1e-9
                and abs(val_nueva - val_vieja) < 1e-6):
            continue  # sin cambio real
        cambios.append({
            "_id":          d["_id"],
            "id_cuenta":    d.get("id_cuenta"),
            "unidad":       str(unidad),
            "tipo":         d.get("tipoTitulo"),
            "precio_viejo": precio_viejo,
            "precio_nuevo": precio_nuevo,
            "val_vieja":    val_vieja,
            "val_nueva":    val_nueva,
        })

    print(f"Posiciones a corregir:                       {len(cambios)}")
    print(f"Unidades del snapshot SIN precio en el doc:   {len(sin_precio)}")
    print("-" * 88)
    for c in cambios[:80]:
        u = c["unidad"][:46]
        print(f"  [{str(c['id_cuenta']):>6}] {u:46}  "
              f"precio {c['precio_viejo']:>14,.4f} -> {c['precio_nuevo']:>14,.4f}  "
              f"val {c['val_vieja']:>16,.2f} -> {c['val_nueva']:>16,.2f}")
    if len(cambios) > 80:
        print(f"  ... y {len(cambios) - 80} cambios mas")

    if sin_precio:
        muestra = sorted(u for u in sin_precio if u and u != "None")[:30]
        print("-" * 88)
        print(f"Unidades del snapshot que NO estan en el doc (NO se tocan), muestra:")
        for u in muestra:
            print(f"  - {u}")
        if len(sin_precio) > 30:
            print(f"  ... y {len(sin_precio) - 30} mas")

    # Acumular las unidades faltantes en docs/precios_faltantes.json — una
    # lista plana [{fecha_snapshot, unidad}, ...] que va creciendo corrida
    # tras corrida. SOLO en corridas de snapshot completo: con --cuenta el
    # set de faltantes es parcial y no debe pisar el doc global.
    if args.cuenta:
        print("(--cuenta: corrida parcial — no se toca docs/precios_faltantes.json)")
    else:
        faltantes_path = os.path.join(_REPO_ROOT, "docs", "precios_faltantes.json")
        faltantes: list[dict] = []
        if os.path.exists(faltantes_path):
            try:
                with open(faltantes_path, encoding="utf-8") as f:
                    cargado = json.load(f)
                if isinstance(cargado, list):
                    faltantes = [r for r in cargado if isinstance(r, dict)]
            except (json.JSONDecodeError, OSError):
                faltantes = []
        # Saca las filas de este snapshot (se recalculan) y deja las demás.
        faltantes = [r for r in faltantes if r.get("fecha_snapshot") != args.snapshot]
        for u in sorted(x for x in sin_precio if x and x != "None"):
            faltantes.append({"fecha_snapshot": args.snapshot, "unidad": u})
        faltantes.sort(key=lambda r: (r.get("fecha_snapshot", ""), r.get("unidad", "")))
        with open(faltantes_path, "w", encoding="utf-8") as f:
            json.dump(faltantes, f, ensure_ascii=False, indent=1)
        n_este = sum(1 for r in faltantes if r.get("fecha_snapshot") == args.snapshot)
        print(f"Faltantes acumuladas en docs/precios_faltantes.json "
              f"(total {len(faltantes)} filas; este snapshot: {n_este}).")

    if not args.apply:
        print("=" * 88)
        print(f"DRY-RUN. {len(cambios)} posiciones se corregirian. "
              f"Revisa que las valuaciones nuevas tengan sentido y corre con --apply.")
        return

    ops = [
        UpdateOne(
            {"_id": c["_id"]},
            {"$set": {"precio": c["precio_nuevo"], "valuacion": c["val_nueva"]}},
        )
        for c in cambios
    ]
    if ops:
        res = col.bulk_write(ops, ordered=False)
        print("=" * 88)
        print(f"APLICADO: {res.modified_count} docs modificados en Valuaciones.AuM.")
    else:
        print("Nada que aplicar.")


if __name__ == "__main__":
    main()
