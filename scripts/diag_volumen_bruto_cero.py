"""Diag READ-ONLY — ¿qué son los docs con bruto=0 en Operaciones y cuál es el gap
real de mep para pesificar el volumen?

Contexto: para migrar el volumen de operadores a Operaciones, "volumen = Σ bruto".
Pero ~300k docs tienen bruto=0. Hipótesis: son los futuros (sin bruto en pesos),
que comercial YA excluía del volumen. Esto lo confirma con dato:

  1. Desglose de los bruto=0 por mercado y por operacion (¿futuros?).
  2. Desglose de los bruto>0 por mercado y por operacion (lo que SÍ es volumen).
  3. USD con bruto>0: cuántos quedan SIN mep del día (gap real de pesificación) +
     rango de fechas del gap.

NO escribe nada. Solo lectura.

Uso:
    python -m scripts.diag_volumen_bruto_cero
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read

_OPS_NO_CIERRE = {"$not": {"$regex": "Cierre", "$options": "i"}}
_BASE = {"tipo_operacion": _OPS_NO_CIERRE, "etapa": {"$ne": "solicitud"}}


def _grupo(coll, match: dict, campo: str, top: int = 15) -> list:
    return list(coll.aggregate([
        {"$match": match},
        {"$group": {"_id": f"${campo}", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}}, {"$limit": top},
    ]))


def _print(titulo: str, rows: list) -> None:
    print(f"\n── {titulo} ──")
    for r in rows:
        print(f"   {str(r['_id'])[:50]:<52}{r['n']:>9}")


def main() -> None:
    cash = get_mongo_client_read()["CashFlow"]
    ops = cash["Operaciones"]
    mov = cash["NegocioMovimientos"]

    cero = {**_BASE, "$or": [{"bruto": 0}, {"bruto": None}]}
    pos = {**_BASE, "bruto": {"$gt": 0}}

    print("══ bruto = 0 (¿qué son?) ══")
    _print("bruto=0 · por mercado", _grupo(ops, cero, "mercado"))
    _print("bruto=0 · por operacion", _grupo(ops, cero, "operacion"))

    print("\n\n══ bruto > 0 (lo que SÍ es volumen) ══")
    _print("bruto>0 · por mercado", _grupo(ops, pos, "mercado"))
    _print("bruto>0 · por operacion", _grupo(ops, pos, "operacion"))

    # ── gap de mep para USD con bruto>0 ──
    print("\n\n══ Gap de mep · USD con bruto>0 ══")
    mep_fechas = {d["_id"] for d in mov.aggregate([
        {"$match": {"mep": {"$gt": 0}}}, {"$group": {"_id": "$fecha"}}])}
    n_usd_pos = ops.count_documents({**pos, "moneda": "USD"})
    sin_mep = 0
    fechas_gap: set[str] = set()
    for d in ops.find({**pos, "moneda": "USD"}, {"_id": 0, "concertacion": 1}):
        f = d.get("concertacion")
        if f not in mep_fechas:
            sin_mep += 1
            fechas_gap.add(f)
    print(f"  USD con bruto>0: {n_usd_pos}")
    print(f"  de esos, SIN mep del día: {sin_mep}")
    if fechas_gap:
        fs = sorted(x for x in fechas_gap if x)
        print(f"  rango de fechas sin mep: {fs[0]} → {fs[-1]} ({len(fs)} fechas)")

    print("\n(read-only: no se escribió nada)")


if __name__ == "__main__":
    main()
