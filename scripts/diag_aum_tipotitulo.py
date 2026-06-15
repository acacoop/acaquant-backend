"""scripts/diag_aum_tipotitulo.py — READ-ONLY. Por qué se ÷100 el cash en pesos.

El divisor (÷100 vs ×1) lo decide `tipoTitulo` en jobs/aum.py::_calcular_valuacion.
`portafolio.tenencia` NO guarda tipoTitulo, pero Mongo `Valuaciones.AuM` SÍ. Este
diag mira la última fecha de Mongo AuM y muestra, por tipoTitulo, el divisor que se
aplicó y ejemplos — para cazar el tipoTitulo que está ÷100 al cash/monedas (precio≈1).

    python -m scripts.diag_aum_tipotitulo              # última fecha_snapshot
    python -m scripts.diag_aum_tipotitulo 2025-06-30   # una fecha puntual
"""
from __future__ import annotations

import sys
from collections import defaultdict

from core.mongo import get_mongo_client_read


def _div(precio, cantidad, valuacion):
    try:
        p, c, v = float(precio), float(cantidad), float(valuacion)
    except (TypeError, ValueError):
        return None
    if not v or not p or not c:
        return None
    r = (p * c) / v
    for d in (1, 100):
        if abs(r - d) / d < 0.01:
            return d
    return round(r, 2)


def main() -> None:
    col = get_mongo_client_read()["Valuaciones"]["AuM"]
    fecha = sys.argv[1] if len(sys.argv) > 1 else None
    if not fecha:
        ult = list(col.find({}, {"fecha_snapshot": 1}).sort("fecha_snapshot", -1).limit(1))
        fecha = ult[0]["fecha_snapshot"] if ult else None
    print(f"\n=== Mongo Valuaciones.AuM · fecha_snapshot {fecha} ===\n")

    docs = list(col.find({"fecha_snapshot": fecha},
                         {"_id": 0, "tipoTitulo": 1, "unidad": 1, "cantidad": 1,
                          "precio": 1, "valuacion": 1, "moneda": 1, "cuenta": 1}))
    if not docs:
        print("Sin docs para esa fecha.")
        return

    # por tipoTitulo: conteo de divisores + un ejemplo + cuántos son 'cash' (precio≈1)
    por_tipo: dict[str, dict] = defaultdict(
        lambda: {"n": 0, "divs": defaultdict(int), "cash": 0, "ej": None})
    for d in docs:
        tipo = d.get("tipoTitulo") or "(vacío)"
        div = _div(d.get("precio"), d.get("cantidad"), d.get("valuacion"))
        g = por_tipo[tipo]
        g["n"] += 1
        g["divs"][div] += 1
        try:
            if abs(float(d.get("precio") or 0) - 1.0) < 0.001:
                g["cash"] += 1
        except (TypeError, ValueError):
            pass
        if g["ej"] is None:
            g["ej"] = d

    print(f"{'tipoTitulo':<42}{'N':>6}{'cash(p≈1)':>10}  DIVISOR(conteo)")
    for tipo in sorted(por_tipo, key=lambda t: -por_tipo[t]["n"]):
        g = por_tipo[tipo]
        divs = ", ".join(f"÷{k}×{v}" for k, v in sorted(g["divs"].items(), key=lambda x: -x[1]))
        flag = "  <-- CASH ÷100 ¿MAL?" if g["cash"] and 100 in g["divs"] else ""
        print(f"{tipo:<42}{g['n']:>6}{g['cash']:>10}  {divs}{flag}")

    # Ejemplos concretos de cash ÷100 (lo que mostró el user: precio 1, val=cant/100)
    print("\nEjemplos de posiciones precio≈1 que quedaron ÷100 (deberían ×1):")
    n = 0
    for d in docs:
        try:
            p = float(d.get("precio") or 0)
        except (TypeError, ValueError):
            continue
        if abs(p - 1.0) < 0.001 and _div(d.get("precio"), d.get("cantidad"), d.get("valuacion")) == 100:
            print(f"   unidad={d.get('unidad')!r:<22} tipo={d.get('tipoTitulo')!r:<28} "
                  f"moneda={d.get('moneda')!r} cant={d.get('cantidad')} val={d.get('valuacion')}")
            n += 1
            if n >= 15:
                break
    if not n:
        print("   (ninguna en esta fecha)")


if __name__ == "__main__":
    main()
