"""fix_fci_resumen_fechas.py — corrige fecha_snapshot en AuMResumenFCI.

Bug: el rollup `Valuaciones.AuMResumenFCI` tiene docs viejos con
`fecha_snapshot` rotulada al dia 1 del mes (2025-07-01, ...) mientras que
la fuente raw `Valuaciones.AuM` tiene esos mismos snapshots a fin de mes
(2025-07-31, ...). El selector del frontend sale del resumen -> ofrece
dia-1; el detalle (`fci_snapshot`, match exacto contra AuM) no encuentra
nada -> "Total FCI $0".

Fix: para cada doc del resumen cuya `fecha_snapshot` NO exista en AuM
(unidades FCI), buscar las fechas FCI de AuM del MISMO MES. Si hay
exactamente una candidata (y no colisiona con otro doc del resumen),
corregir el doc del resumen a esa fecha.

Dry-run por default — solo imprime. Pasa --apply para escribir.

Corre:  python -m scripts.fix_fci_resumen_fechas [--apply]
"""
from __future__ import annotations

import sys

from api.services.portfolio import _fci_assets_map
from core.mongo import get_mongo_client


def main() -> None:
    apply = "--apply" in sys.argv
    db = get_mongo_client()["Valuaciones"]

    assets_map = _fci_assets_map()
    unidades_fci = list(assets_map.keys())
    if not unidades_fci:
        print("Sin unidades FCI (_fci_assets_map vacio). Corto.")
        return

    # Fechas FCI reales en AuM, agrupadas por mes.
    fechas_aum: set[str] = set()
    for d in db["AuM"].find(
        {"unidad": {"$in": unidades_fci}},
        {"_id": 0, "fecha_snapshot": 1},
    ):
        fs = d.get("fecha_snapshot")
        if isinstance(fs, str):
            fechas_aum.add(fs)
    por_mes: dict[str, list[str]] = {}
    for f in fechas_aum:
        por_mes.setdefault(f[:7], []).append(f)

    print(f"{len(fechas_aum)} fechas FCI distintas en Valuaciones.AuM.")
    print(f"MODO: {'APPLY (escribe)' if apply else 'DRY-RUN (solo imprime)'}\n")

    docs = list(db["AuMResumenFCI"].find({}, {"_id": 1, "fecha_snapshot": 1}))
    fechas_resumen = {
        d.get("fecha_snapshot") for d in docs
        if isinstance(d.get("fecha_snapshot"), str)
    }

    n_ok = n_fix = n_skip = 0
    for doc in docs:
        fecha = doc.get("fecha_snapshot")
        if not isinstance(fecha, str):
            print(f"  SKIP  _id={doc['_id']}  fecha_snapshot no es str: {fecha!r}")
            n_skip += 1
            continue
        if fecha in fechas_aum:
            n_ok += 1
            continue
        # La fecha del resumen no existe en AuM -> buscar el mes.
        mes = fecha[:7]
        candidatas = sorted(por_mes.get(mes, []))
        if len(candidatas) != 1:
            print(f"  SKIP  {fecha!r}  mes {mes} tiene {len(candidatas)} "
                  f"fechas en AuM ({candidatas}) — ambiguo, no toco.")
            n_skip += 1
            continue
        nueva = candidatas[0]
        if nueva in fechas_resumen:
            print(f"  SKIP  {fecha!r} -> {nueva!r}  ya existe otro doc del "
                  f"resumen con esa fecha — colision, no toco.")
            n_skip += 1
            continue
        print(f"  FIX   {fecha!r}  ->  {nueva!r}")
        n_fix += 1
        if apply:
            db["AuMResumenFCI"].update_one(
                {"_id": doc["_id"]},
                {"$set": {"fecha_snapshot": nueva}},
            )

    print(f"\nResumen: {n_ok} ya OK, {n_fix} {'corregidos' if apply else 'a corregir'}, "
          f"{n_skip} skip.")
    if n_fix and not apply:
        print("Volve a correr con --apply para escribir los cambios.")


if __name__ == "__main__":
    main()
