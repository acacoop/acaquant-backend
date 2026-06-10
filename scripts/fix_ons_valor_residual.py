"""scripts/fix_ons_valor_residual.py — restaura valor_residual en ONs DL.

CORRIGE un daño que metió fix_ons_flujo_a_per100 en su 1ra versión: además de
los pagos (amort/interes), dividió `valor_residual` por el factor → quedó en
~0,07 en vez de ~100 → la paridad explotó (144.500%).

Recalcula valor_residual[i] = Σ(amortizacion[i:]) = el nominal que aún falta
amortizar a partir de ese flujo. Es la definición correcta del residual y es
independiente del campo viejo (corrupto o no). Idempotente.

  (dry-run)  python -m scripts.fix_ons_valor_residual
  (aplica)   python -m scripts.fix_ons_valor_residual --commit

ALCANCE: ONs (curva ^on) con moneda_flujo=DL. Bumpea ingestado_en.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime

from pymongo import UpdateOne

from core.mongo import get_mongo_client


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="aplica la corrección")
    args = ap.parse_args()

    trading = get_mongo_client()["Trading"]
    ons = list(trading["Curvas"].find(
        {"curva": {"$regex": "^on"}, "moneda_flujo": "DL"},
        {"_id": 0, "ticker_corto": 1, "flujos": 1}))

    ops: list[UpdateOne] = []
    ahora = datetime.now(UTC)
    print(f"{'ticker':<9}{'#flujos':>8}{'resid[0]_antes':>16}{'resid[0]_desp':>15}")
    print("-" * 50)

    for o in sorted(ons, key=lambda x: x.get("ticker_corto", "")):
        tc = o.get("ticker_corto", "")
        flujos = o.get("flujos") or []
        if not flujos:
            continue
        amorts = [_f(f.get("amortizacion")) for f in flujos]
        # residual[i] = suma de amortizaciones desde i hasta el final.
        nuevos = []
        cambio = False
        for i, f in enumerate(flujos):
            resid = round(sum(amorts[i:]), 6)
            nf = dict(f)
            if _f(nf.get("valor_residual")) != resid:
                cambio = True
            nf["valor_residual"] = resid
            nuevos.append(nf)
        if not cambio:
            continue

        antes = _f(flujos[0].get("valor_residual"))
        desp = _f(nuevos[0].get("valor_residual"))
        print(f"{tc:<9}{len(flujos):>8}{antes:>16,.4f}{desp:>15,.4f}")
        ops.append(UpdateOne(
            {"ticker_corto": tc},
            {"$set": {"flujos": nuevos, "ingestado_en": ahora}}))

    print("\n" + "=" * 50)
    print(f"{len(ops)} ONs DL a corregir valor_residual.")
    if not ops:
        print("✅ Nada que corregir — valor_residual ya = Σ amort futura.")
        return
    if not args.commit:
        print("\n(DRY-RUN — no se escribió nada. resid[0]_desp debería dar ~100. Corré --commit.)")
        return

    res = trading["Curvas"].bulk_write(ops, ordered=False)
    print(f"\n✅ {res.modified_count} ONs corregidas. Reiniciá motor_curvas:  "
          "sudo systemctl restart motor_curvas")


if __name__ == "__main__":
    main()
