"""scripts/diag_aum_congelado.py — detecta tenencias CONGELADAS en Valuaciones.AuM.

Read-only. Para una cuenta:
  1. DETALLE de un ticker (default GD35): imprime cantidad/precio/valuación por
     fecha_snapshot → se ve si quedan idénticos día a día.
  2. RESUMEN de cuán generalizado está: por cada unidad con ≥2 snapshots, cuenta
     cuántas tienen VALUACIÓN / PRECIO / CANTIDAD idéntica en TODOS los días.
     - PRECIO idéntico día a día en un bono = BUG (el precio se mueve siempre).
     - VALUACIÓN idéntica = la tenencia está congelada.

Uso:
    python -m scripts.diag_aum_congelado                       # cuenta 255, ticker GD35
    python -m scripts.diag_aum_congelado --cuenta 255 --ticker GD35
    python -m scripts.diag_aum_congelado --cuenta 805 --ticker AL30
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict

from core.mongo import get_mongo_client_read


def _arg(flag: str, default: str) -> str:
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def _num(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def main() -> int:
    cuenta = _arg("--cuenta", "255")
    ticker = _arg("--ticker", "GD35")

    db = get_mongo_client_read()["Valuaciones"]
    docs = list(db["AuM"].find(
        {"id_cuenta": str(cuenta)},
        {"_id": 0, "fecha_snapshot": 1, "unidad": 1, "cantidad": 1,
         "precio": 1, "valuacion": 1},
    ))
    by_unidad: dict[str, list[dict]] = defaultdict(list)
    for d in docs:
        by_unidad[d.get("unidad") or "?"].append(d)

    fechas = sorted({d.get("fecha_snapshot") for d in docs if d.get("fecha_snapshot")})
    print(f"=== cuenta {cuenta} · {len(docs)} filas AuM · {len(by_unidad)} unidades · "
          f"{len(fechas)} fechas ({fechas[0] if fechas else '—'} → {fechas[-1] if fechas else '—'}) ===")

    # 1) DETALLE del ticker pedido
    print(f"\n--- DETALLE {ticker} (últimos 15 snapshots) ---")
    rx = re.compile(re.escape(ticker), re.I)
    encontrado = False
    for u, lst in by_unidad.items():
        if not rx.search(u):
            continue
        encontrado = True
        lst.sort(key=lambda x: x.get("fecha_snapshot") or "")
        print(f"\nunidad: {u}   ({len(lst)} snapshots)")
        print(f"  {'FECHA':<12} {'CANTIDAD':>16} {'PRECIO':>16} {'VALUACION':>18}")
        for d in lst[-15:]:
            print(f"  {d.get('fecha_snapshot')!s:<12} {_num(d.get('cantidad')):>16,.4f} "
                  f"{_num(d.get('precio')):>16,.4f} {_num(d.get('valuacion')):>18,.2f}")
        vals = {round(_num(d.get('valuacion')), 2) for d in lst}
        precios = {round(_num(d.get('precio')), 4) for d in lst}
        print(f"  >>> valores distintos de VALUACIÓN: {len(vals)}   de PRECIO: {len(precios)}"
              f"   {'← CONGELADO' if len(precios) <= 1 and len(lst) > 1 else ''}")
    if not encontrado:
        print(f"  (no se encontró ninguna unidad que matchee '{ticker}' en la cuenta {cuenta})")

    # 2) RESUMEN: ¿cuán generalizado?
    print(f"\n--- RESUMEN congelados (cuenta {cuenta}, unidades con ≥2 snapshots) ---")
    total = cong_val = cong_precio = cong_cant = 0
    congeladas: list[str] = []
    for u, lst in by_unidad.items():
        if len(lst) < 2:
            continue
        total += 1
        nv = len({round(_num(d.get('valuacion')), 2) for d in lst})
        np = len({round(_num(d.get('precio')), 4) for d in lst})
        nc = len({round(_num(d.get('cantidad')), 4) for d in lst})
        if nv == 1:
            cong_val += 1
        if np == 1:
            cong_precio += 1
            congeladas.append(u)
        if nc == 1:
            cong_cant += 1
    print(f"  unidades evaluadas: {total}")
    print(f"  PRECIO idéntico TODOS los días: {cong_precio}  ← BUG (un precio se mueve siempre)")
    print(f"  VALUACIÓN idéntica TODOS los días: {cong_val}  ← tenencia congelada")
    print(f"  CANTIDAD idéntica: {cong_cant}  (normal si no hubo trades)")
    if congeladas:
        print("\n  Unidades con PRECIO congelado (muestra 20):")
        for u in congeladas[:20]:
            print(f"    - {u}")
        if len(congeladas) > 20:
            print(f"    … +{len(congeladas) - 20} más")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
