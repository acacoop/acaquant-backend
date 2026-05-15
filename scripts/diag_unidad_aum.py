"""diag_unidad_aum.py — inspecciona unidades de Valuaciones.AuM.

Para un patrón de unidad muestra tipoTitulo / precio / cantidad /
valuacion en los snapshots donde aparece. Además lista TODOS los
tipoTitulo distintos de la colección, marcando cuáles caen en el divisor
/100 (TIPOS_DIVISOR_100 de jobs/aum.py).

Sirve para detectar instrumentos mal clasificados: si un bono tiene un
tipoTitulo que NO está en TIPOS_DIVISOR_100, su valuación queda inflada
x100 (precio × cantidad sin dividir).

NO modifica nada. Corre:
    python -m scripts.diag_unidad_aum "D1636"
    python -m scripts.diag_unidad_aum "[9327]"
"""
from __future__ import annotations

import sys
from collections import Counter

from core.mongo import get_mongo_client
from jobs.aum import TIPOS_DIVISOR_100, TIPOS_FUTUROS


def _clasificar(tipo: object) -> str:
    t = str(tipo)
    if t in TIPOS_DIVISOR_100:
        return "DIVIDE /100"
    if any(f.lower() in t.lower() for f in TIPOS_FUTUROS):
        return "FUTURO (precio+1)"
    return "SIN DIVISOR (precio x cantidad crudo)"


def main() -> None:
    patron = sys.argv[1] if len(sys.argv) > 1 else None
    col = get_mongo_client()["Valuaciones"]["AuM"]

    if patron:
        print("=" * 78)
        print(f"Unidades que matchean {patron!r} en Valuaciones.AuM")
        print("=" * 78)
        docs = list(col.find(
            {"unidad": {"$regex": patron, "$options": "i"}},
            {"_id": 0, "unidad": 1, "tipoTitulo": 1, "fecha_snapshot": 1,
             "id_cuenta": 1, "cantidad": 1, "precio": 1, "valuacion": 1},
        ).sort("fecha_snapshot", 1))
        print(f"{len(docs)} docs encontrados.\n")

        por_ut: dict[tuple, list] = {}
        for d in docs:
            por_ut.setdefault((d.get("unidad"), d.get("tipoTitulo")), []).append(d)

        for (unidad, tipo), grupo in sorted(por_ut.items(), key=lambda x: str(x[0])):
            print(f"  unidad     = {unidad!r}")
            print(f"  tipoTitulo = {tipo!r}")
            print(f"  -> {_clasificar(tipo)}")
            for d in grupo[:10]:
                try:
                    val = float(d.get("valuacion") or 0)
                except (TypeError, ValueError):
                    val = 0.0
                print(f"     {d.get('fecha_snapshot')}  cta=[{d.get('id_cuenta')}]  "
                      f"cant={d.get('cantidad')}  precio={d.get('precio')}  "
                      f"valuacion={val:,.2f}")
            if len(grupo) > 10:
                print(f"     ... y {len(grupo) - 10} docs mas")
            print()

    # Listado global de tipoTitulo — para detectar tipos mal clasificados.
    print("=" * 78)
    print("TODOS los tipoTitulo distintos en Valuaciones.AuM (con su clasificación):")
    print("=" * 78)
    tipos: Counter = Counter()
    for d in col.find({}, {"_id": 0, "tipoTitulo": 1}):
        tipos[str(d.get("tipoTitulo"))] += 1
    for tipo, n in tipos.most_common():
        print(f"  {_clasificar(tipo):40}  {n:8d}  {tipo}")


if __name__ == "__main__":
    main()
