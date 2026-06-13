"""scripts/diag_aum_cantidad_vs_movs.py — ¿cantidad congelada = bug o tenencia normal?

Read-only. Para una cuenta:
  1. Detecta unidades con CANTIDAD idéntica en todos sus snapshots de Valuaciones.AuM.
  2. Por cada una, cruza contra CashFlow.NegocioMovimientos: ¿hubo compras/ventas/
     suscripciones/rescates de esa especie en el rango de fechas del AuM?
  3. Veredicto por unidad:
       - con movimientos + cantidad PLANA  → ⚠ BUG (operó pero no se actualizó).
       - sin movimientos + cantidad plana  → OK (buy & hold, congelado correcto).

Match unidad↔ticker vía Valuaciones.Assets (unidad → TICKER) → NegocioMovimientos.ticker.

Uso:
    python -m scripts.diag_aum_cantidad_vs_movs                 # cuenta 255
    python -m scripts.diag_aum_cantidad_vs_movs --cuenta 805
"""
from __future__ import annotations

import sys
from collections import defaultdict

from core.mongo import get_mongo_client_read

_CATS = ["compra", "venta", "rescate_fci", "suscripcion_fci"]


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
    db = get_mongo_client_read()["Valuaciones"]
    cf = get_mongo_client_read()["CashFlow"]

    docs = list(db["AuM"].find(
        {"id_cuenta": str(cuenta)},
        {"_id": 0, "fecha_snapshot": 1, "unidad": 1, "cantidad": 1},
    ))
    by_unidad: dict[str, list[dict]] = defaultdict(list)
    for d in docs:
        by_unidad[d.get("unidad") or "?"].append(d)
    fechas = sorted({d.get("fecha_snapshot") for d in docs if d.get("fecha_snapshot")})
    if not fechas:
        print(f"  (sin AuM para la cuenta {cuenta})")
        return 0
    f_min, f_max = fechas[0], fechas[-1]

    # unidad → TICKER (Assets)
    uni2tk: dict[str, str] = {}
    for a in db["Assets"].find({}, {"_id": 0, "unidad": 1, "TICKER": 1}):
        if a.get("unidad"):
            uni2tk[a["unidad"]] = (a.get("TICKER") or "").strip()

    print(f"=== cuenta {cuenta} · rango AuM {f_min} → {f_max} ===")
    print("Unidades con CANTIDAD congelada (idéntica en todos los snapshots) + "
          "¿operó en el rango?\n")
    print(f"  {'UNIDAD':<40} {'CANT':>14} {'#SNAPS':>7} {'#MOVS':>6}  VEREDICTO")

    bug = ok = 0
    for u, lst in sorted(by_unidad.items()):
        if len(lst) < 2:
            continue
        cants = {round(_num(d.get('cantidad')), 4) for d in lst}
        if len(cants) != 1:
            continue  # la cantidad SÍ cambió → no nos interesa acá
        cant = next(iter(cants))
        tk = uni2tk.get(u, "")
        nmov = 0
        if tk:
            nmov = cf["NegocioMovimientos"].count_documents({
                "id_cuenta": str(cuenta),
                "ticker": tk,
                "categoria": {"$in": _CATS},
                "fecha": {"$gte": f_min, "$lte": f_max},
            })
        if nmov > 0:
            veredicto = "⚠ BUG (operó y la cantidad no cambió)"
            bug += 1
        else:
            veredicto = "OK (sin trades, congelado normal)"
            ok += 1
        print(f"  {u[:40]:<40} {cant:>14,.2f} {len(lst):>7} {nmov:>6}  {veredicto}")

    print(f"\n  RESUMEN: {bug} con BUG (operó pero cantidad plana) · {ok} OK (sin trades).")
    if bug:
        print("  ⚠ Hay especies que operaron y la cantidad NO se actualizó → confirma el problema.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
