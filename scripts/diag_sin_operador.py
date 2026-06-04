"""scripts/diag_sin_operador.py — ¿Qué cuentas son el bucket "(sin operador)" del
ranking comercial? Listarlas para asignarles operador (REGLA #2, read-only).

El ranking de volumen (informe_comercial) agrupa por operador y mete en
"(sin operador)" a las cuentas cuyo `operador_email` está vacío
(`(operador_email or "").strip()` → null/ausente/""/whitespace). Ese bucket
puede rankear alto. Este diag replica EXACTO ese criterio y lista las cuentas
con su volumen, para ver cuáles son y por qué no tienen operador.

Muestra por cuenta: volumen pesificado, denominación, el valor CRUDO de
operador_email (para ver si es null / "" / " "), estado y si está en Comitentes.

Read-only. Correr en el Droplet:
    python -m scripts.diag_sin_operador
    python -m scripts.diag_sin_operador --top 50
"""
from __future__ import annotations

import argparse

from api.services._negocio_futuros import match_no_futuros
from api.services.comercial import _CATS_VOLUMEN, _PESIF
from core.mongo import get_mongo_client_read


def _vacio(email) -> bool:
    return not str(email or "").strip()


def run(top: int) -> dict:
    db = get_mongo_client_read()
    nm = db["CashFlow"]["NegocioMovimientos"]
    com = db["Clientes"]["Comitentes"]

    # Volumen pesificado por id_cuenta (mismo match que informe_comercial).
    vol_por_cuenta = {
        str(r["_id"]): float(r["v"] or 0.0)
        for r in nm.aggregate([
            {"$match": {"categoria": {"$in": list(_CATS_VOLUMEN)}, **match_no_futuros()}},
            {"$group": {"_id": "$id_cuenta", "v": {"$sum": _PESIF}}},
        ], allowDiskUse=True)
        if r.get("_id")
    }
    # Detalle de Comitentes (todas, no solo Activa, para ver el caso).
    detalle = {
        str(d["id_cuenta"]): d
        for d in com.find({}, {"_id": 0, "id_cuenta": 1, "denominacion": 1,
                               "operador_email": 1, "estado": 1})
        if d.get("id_cuenta")
    }

    # Bucket "(sin operador)": cuentas con volumen cuyo operador (en Comitentes)
    # está vacío, O que no están en Comitentes (info ausente → "(sin operador)").
    filas = []
    for idc, vol in vol_por_cuenta.items():
        info = detalle.get(idc)
        email = (info or {}).get("operador_email")
        if info is not None and not _vacio(email):
            continue  # tiene operador → no es del bucket
        filas.append({
            "id_cuenta": idc,
            "vol": round(vol, 2),
            "denominacion": (info or {}).get("denominacion") or "(no está en Comitentes)",
            "operador_email_raw": repr(email),  # None / '' / '  ' a la vista
            "estado": (info or {}).get("estado"),
            "en_comitentes": info is not None,
        })
    filas.sort(key=lambda f: f["vol"], reverse=True)

    total_vol = round(sum(f["vol"] for f in filas), 2)
    print(f"Cuentas con volumen y SIN operador: {len(filas)} · "
          f"volumen total del bucket = {total_vol:,.0f}\n")
    print(f"  {'id_cuenta':12} {'volumen':>18} {'estado':10} {'en_com':7} "
          f"{'operador_email':16} denominación")
    for f in filas[:top]:
        print(f"  {f['id_cuenta']:12} {f['vol']:>18,.0f} {f['estado'] or '—'!s:10} "
              f"{'sí' if f['en_comitentes'] else 'NO':7} {f['operador_email_raw']:16} "
              f"{f['denominacion']}")
    if len(filas) > top:
        print(f"  … +{len(filas) - top} más (usá --top N)")
    return {"cuentas": len(filas), "volumen_total": total_vol}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=30, help="cuántas filas mostrar (default 30)")
    args = ap.parse_args()
    res = run(args.top)
    print(f"\n→ {res}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
