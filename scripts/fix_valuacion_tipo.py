"""fix_valuacion_tipo.py — recalcula la valuacion de Valuaciones.AuM para
un tipoTitulo dado.

Se usa cuando un tipoTitulo estaba mal clasificado en jobs/aum.py (no
dividía /100) y se corrigió: los docs viejos quedaron con la valuación
inflada x100 y hay que recalcularlos en todos los snapshots.

NO toca el precio — solo recalcula `valuacion` con la lógica ACTUAL de
jobs/aum.py::_calcular_valuacion (que ya incluye el fix). Recorre todos
los snapshots donde aparece el tipo.

Default: 'Letras de Liquidez del Banco Central' (LELIQ/LEFI — caso
[9327] D16E6, que no dividía /100 e inflaba la valuación x100).

Dry-run por default. Pasa --apply para escribir.

Corre:  python -m scripts.fix_valuacion_tipo [--tipo "..."] [--apply]
"""
from __future__ import annotations

import argparse

from pymongo import UpdateOne

from core.mongo import get_mongo_client
from jobs.aum import _calcular_valuacion

_TIPO_DEFAULT = "Letras de Liquidez del Banco Central"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tipo", default=_TIPO_DEFAULT,
                    help=f"tipoTitulo a recalcular (default {_TIPO_DEFAULT!r})")
    ap.add_argument("--apply", action="store_true", help="escribe los cambios")
    args = ap.parse_args()

    col = get_mongo_client()["Valuaciones"]["AuM"]
    docs = list(col.find({"tipoTitulo": args.tipo}))

    print(f"tipoTitulo: {args.tipo!r}")
    print(f"Modo:       {'APPLY (escribe)' if args.apply else 'DRY-RUN'}")
    print(f"Docs encontrados: {len(docs)}")
    print("=" * 88)

    cambios: list[dict] = []
    for d in docs:
        val_vieja = float(d.get("valuacion") or 0)
        val_nueva = float(_calcular_valuacion({
            "precio":     d.get("precio"),
            "cantidad":   d.get("cantidad"),
            "tipoTitulo": d.get("tipoTitulo"),
        }))
        if abs(val_nueva - val_vieja) < 1e-6:
            continue
        cambios.append({
            "_id":       d["_id"],
            "id_cuenta": d.get("id_cuenta"),
            "unidad":    d.get("unidad"),
            "fecha":     d.get("fecha_snapshot"),
            "val_vieja": val_vieja,
            "val_nueva": val_nueva,
        })

    print(f"Docs a recalcular: {len(cambios)}")
    print("-" * 88)
    for c in cambios[:80]:
        u = str(c["unidad"])[:34]
        print(f"  {c['fecha']}  [{c['id_cuenta']!s:>6}] {u:34}  "
              f"val {c['val_vieja']:>22,.2f} -> {c['val_nueva']:>20,.2f}")
    if len(cambios) > 80:
        print(f"  ... y {len(cambios) - 80} mas")

    if not args.apply:
        print("=" * 88)
        print(f"DRY-RUN. {len(cambios)} docs se recalcularian. Corre con --apply.")
        return

    ops = [
        UpdateOne({"_id": c["_id"]}, {"$set": {"valuacion": c["val_nueva"]}})
        for c in cambios
    ]
    if ops:
        res = col.bulk_write(ops, ordered=False)
        print("=" * 88)
        print(f"APLICADO: {res.modified_count} docs recalculados en Valuaciones.AuM.")
    else:
        print("Nada que aplicar.")


if __name__ == "__main__":
    main()
