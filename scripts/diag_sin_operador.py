"""scripts/diag_sin_operador.py — ¿Qué cuentas son el bucket "(sin operador)" del
ranking comercial? Listarlas Y CATEGORIZARLAS antes de tocar el ranking (REGLA #2).

El ranking de volumen (informe_comercial) agrupa por operador y mete en
"(sin operador)" a las cuentas con `operador_email` vacío (incluye whitespace) o
que no están en Comitentes. Este diag replica ese criterio y, para las que NO
están en Comitentes, las CLASIFICA con la lógica de jobs/_aum_filters
(propia [100]/[101] / contraparte / FCI / OTC) — así confirmamos que son
no-clientes ANTES de excluirlas del ranking. Las "SIN CLASIFICAR" serían
sospechosas (posibles clientes reales faltantes del master).

Read-only. Correr en el Droplet:
    python -m scripts.diag_sin_operador
    python -m scripts.diag_sin_operador --top 60
"""
from __future__ import annotations

import argparse
from collections import Counter

from api.services._negocio_futuros import match_no_futuros
from api.services.comercial import _CATS_VOLUMEN, _PESIF
from core.mongo import get_mongo_client_read
from jobs._aum_filters import (
    is_excluded,
    load_contrapartes_id_cuentas,
    load_contrapartes_names,
)


def _vacio(email) -> bool:
    return not str(email or "").strip()


def _categoria(cuenta: str | None, unidad: str | None, idc: str,
               ids, names) -> str:
    c = (cuenta or "").upper()
    if c.startswith("[100]") or c.startswith("[101]"):
        return "propia [100]/[101]"
    if idc in ids:
        return "contraparte/FCI (id)"
    if "OTC" in c or "CDC" in c or (unidad or "").upper() == "USDL":
        return "OTC/CDC/USDL"
    if is_excluded(cuenta, unidad, idc, ids, names):
        return "contraparte (nombre)"
    return "SIN CLASIFICAR (¿cliente faltante?)"


def run(top: int) -> dict:
    db = get_mongo_client_read()
    nm = db["CashFlow"]["NegocioMovimientos"]
    com = db["Clientes"]["Comitentes"]

    # Volumen pesificado + nombre/unidad representativos por id_cuenta.
    agg = {
        str(r["_id"]): {"vol": float(r["v"] or 0.0),
                        "cuenta": r.get("cuenta"), "unidad": r.get("unidad")}
        for r in nm.aggregate([
            {"$match": {"categoria": {"$in": list(_CATS_VOLUMEN)}, **match_no_futuros()}},
            {"$group": {"_id": "$id_cuenta", "v": {"$sum": _PESIF},
                        "cuenta": {"$first": "$cuenta"}, "unidad": {"$first": "$unidad"}}},
        ], allowDiskUse=True)
        if r.get("_id")
    }
    detalle = {
        str(d["id_cuenta"]): d
        for d in com.find({}, {"_id": 0, "id_cuenta": 1, "denominacion": 1,
                               "operador_email": 1, "estado": 1})
        if d.get("id_cuenta")
    }
    ids = load_contrapartes_id_cuentas()
    names = load_contrapartes_names()

    en_com_sin_op, no_com = [], []
    for idc, a in agg.items():
        info = detalle.get(idc)
        if info is not None and not _vacio(info.get("operador_email")):
            continue  # cliente con operador → fuera del bucket
        fila = {"id_cuenta": idc, "vol": round(a["vol"], 2), "cuenta": a.get("cuenta")}
        if info is not None:
            fila["denominacion"] = info.get("denominacion")
            fila["estado"] = info.get("estado")
            en_com_sin_op.append(fila)
        else:
            fila["categoria"] = _categoria(a.get("cuenta"), a.get("unidad"), idc, ids, names)
            no_com.append(fila)

    en_com_sin_op.sort(key=lambda f: f["vol"], reverse=True)
    no_com.sort(key=lambda f: f["vol"], reverse=True)

    print("══ A. EN Comitentes pero operador VACÍO (clientes reales → asignar operador) ══")
    if not en_com_sin_op:
        print("  (ninguna) ✓")
    for f in en_com_sin_op[:top]:
        print(f"  {f['id_cuenta']:10} {f['vol']:>16,.0f}  {f.get('estado') or '—':10} {f.get('denominacion') or '—'}")

    print("\n══ B. NO están en Comitentes (no-clientes) — categorizadas ══")
    cat_count: Counter = Counter()
    cat_vol: dict[str, float] = {}
    for f in no_com:
        cat_count[f["categoria"]] += 1
        cat_vol[f["categoria"]] = cat_vol.get(f["categoria"], 0.0) + f["vol"]
    print("  Resumen por categoría:")
    for cat, n in cat_count.most_common():
        print(f"    {cat:34} {n:>4} cuentas · vol {cat_vol[cat]:>16,.0f}")
    print(f"\n  Detalle (top {top}):")
    print(f"  {'id_cuenta':10} {'volumen':>16}  {'categoría':34} cuenta (cruda)")
    for f in no_com[:top]:
        print(f"  {f['id_cuenta']:10} {f['vol']:>16,.0f}  {f['categoria']:34} {f.get('cuenta') or '—'}")

    sin_clasif = sum(1 for f in no_com if f["categoria"].startswith("SIN CLASIFICAR"))
    print(f"\n→ {{'en_comitentes_sin_op': {len(en_com_sin_op)}, "
          f"'no_comitentes': {len(no_com)}, 'sin_clasificar': {sin_clasif}}}")
    if sin_clasif:
        print("  ⚠ Hay cuentas SIN CLASIFICAR → revisar antes de excluir (podrían ser clientes).")
    else:
        print("  ✓ Todas las no-comitentes son no-clientes conocidos → excluir del ranking es seguro.")
    return {"en_comitentes_sin_op": len(en_com_sin_op), "no_comitentes": len(no_com),
            "sin_clasificar": sin_clasif}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=40, help="filas a mostrar por bloque (default 40)")
    args = ap.parse_args()
    run(args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
