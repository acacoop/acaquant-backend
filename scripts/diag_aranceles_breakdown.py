"""Diag READ-ONLY: de dónde sale el arancel en cada fuente (para entender el delta
de la migración NegocioMovimientos → Operaciones).

Desglosa el Σ arancel:
  - Operaciones (NUEVO): por tipo_operacion, por mercado y por operacion.
  - NegocioMovimientos (VIEJO): por op y por categoria.
Con totales por fuente para reconciliar con el diag por operador.

NO escribe nada. Solo lectura.

Uso:
    python -m scripts.diag_aranceles_breakdown
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read

_OPS_MATCH = {
    "arancel": {"$gt": 0},
    "tipo_operacion": {"$not": {"$regex": "Cierre", "$options": "i"}},
    "etapa": {"$ne": "solicitud"},
}
_NEG_MATCH = {"arancel": {"$gt": 0}}


def _por(coll, match: dict, campo: str, top: int = 30) -> tuple[list, float, int]:
    rows = list(coll.aggregate([
        {"$match": match},
        {"$group": {"_id": f"${campo}", "ar": {"$sum": "$arancel"}, "n": {"$sum": 1}}},
        {"$sort": {"ar": -1}},
    ]))
    tot = sum(r["ar"] for r in rows)
    n = sum(r["n"] for r in rows)
    return rows[:top], tot, n


def _m(x: float) -> str:
    return f"{x/1e6:.2f}M" if abs(x) >= 1e6 else f"{x/1e3:.1f}k"


def _print(titulo: str, rows: list, tot: float, n: int) -> None:
    print(f"\n── {titulo} (Σ {_m(tot)} · {n} docs) ──")
    print(f"{'':<46}{'arancel':>12}{'n':>9}{'%':>6}")
    for r in rows:
        ar = r["ar"]
        pct = (100 * ar / tot) if tot else 0
        print(f"{str(r['_id'])[:45]:<46}{_m(ar):>12}{r['n']:>9}{pct:>5.0f}%")


def main() -> None:
    cash = get_mongo_client_read()["CashFlow"]
    ops = cash["Operaciones"]
    mov = cash["NegocioMovimientos"]

    print("═══ NUEVO: CashFlow.Operaciones (sin Cierre, sin etapa=solicitud) ═══")
    r, t, n = _por(ops, _OPS_MATCH, "tipo_operacion")
    _print("Operaciones · por tipo_operacion", r, t, n)
    r2, t2, n2 = _por(ops, _OPS_MATCH, "mercado")
    _print("Operaciones · por mercado", r2, t2, n2)
    r3, t3, n3 = _por(ops, _OPS_MATCH, "operacion")
    _print("Operaciones · por operacion", r3, t3, n3)

    print("\n\n═══ VIEJO: CashFlow.NegocioMovimientos ═══")
    rv, tv, nv = _por(mov, _NEG_MATCH, "op")
    _print("NegocioMov · por op", rv, tv, nv)
    rc, tc, nc = _por(mov, _NEG_MATCH, "categoria")
    _print("NegocioMov · por categoria", rc, tc, nc)

    print("\n\n═══ RECONCILIACIÓN ═══")
    print(f"  Σ arancel Operaciones (NUEVO): {_m(t)}  ({n} docs)")
    print(f"  Σ arancel NegocioMov  (VIEJO): {_m(tv)}  ({nv} docs)")
    print(f"  Δ (NUEVO − VIEJO):             {_m(t - tv)}")

    print("\n(read-only: no se escribió nada)")


if __name__ == "__main__":
    main()
