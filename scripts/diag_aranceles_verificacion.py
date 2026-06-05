"""Diag READ-ONLY: verifica que el arancel de caución ya se cuenta (post-fix).

Compara, en CashFlow.Operaciones, el arancel total con la lógica VIEJA
(es_cierre=False) vs la NUEVA (incluye los cierres con arancel), y lo contrasta
con el rollup OpsSerieDiaria reconstruido. El delta NUEVO−VIEJO = el arancel de
caución que antes se perdía. El rollup debería dar ≈ NUEVO (cubre hasta ayer).

Uso:
    python -m scripts.diag_aranceles_verificacion
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read

_ABS = {"$abs": {"$ifNull": ["$arancel", 0]}}


def _suma(coll, match: dict) -> tuple[float, int]:
    d = next(iter(coll.aggregate([
        {"$match": match},
        {"$group": {"_id": None, "ar": {"$sum": _ABS}, "n": {"$sum": 1}}},
    ])), None)
    return (float(d["ar"]), int(d["n"])) if d else (0.0, 0)


def main() -> int:
    cli = get_mongo_client_read()
    ops = cli["CashFlow"]["Operaciones"]
    rollup = cli["CashFlow"]["OpsSerieDiaria"]

    for moneda in ("ARS", "USD"):
        base = {"moneda": moneda, "etapa": {"$ne": "solicitud"}}
        viejo = {**base, "es_cierre": False}
        nuevo = {**base, "$or": [{"es_cierre": False},
                                 {"es_cierre": True, "arancel": {"$ne": 0}}]}
        av, nv = _suma(ops, viejo)
        an, nn = _suma(ops, nuevo)
        rd = next(iter(rollup.aggregate([
            {"$match": {"moneda": moneda}},
            {"$group": {"_id": None, "ar": {"$sum": "$arancel"}}},
        ])), None)
        ar_rollup = float(rd["ar"]) if rd else 0.0

        print(f"\n== {moneda} ==")
        print(f"  Operaciones VIEJO (es_cierre=False): {av:>18,.2f}  ({nv} ops)")
        print(f"  Operaciones NUEVO (incl. cierre):    {an:>18,.2f}  ({nn} ops)")
        print(f"  → delta caución recuperado:          {an - av:>18,.2f}  ({nn - nv} ops)")
        print(f"  Rollup OpsSerieDiaria (Σ arancel):   {ar_rollup:>18,.2f}  (≈ NUEVO hasta ayer)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
