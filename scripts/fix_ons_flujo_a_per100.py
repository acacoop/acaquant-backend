"""scripts/fix_ons_flujo_a_per100.py — normaliza flujos DL de escala PESO a ~100.

Los DL dólar-linked deben tener el flujo en USD por 100 VN (~100); el motor lleva
el PRECIO a USD (÷A3500) y compara contra ese flujo. Algunos DL se cargaron con
el flujo ya multiplicado por el dólar (escala peso, ej. 144.600 = 100 × 1.446).
Con el precio en USD y el flujo en pesos no matchea → sin TEA.

Este script divide TODO el flujo (amortizacion, interes, valor_residual) por un
factor tal que Σamortizacion quede = 100 (por 100 VN). Ej. VSCIO: 144.600 → 100.
Es la misma conversión peso→USD que el motor hace al precio, pero independiente
del TC (usa la propia escala del flujo), así que recupera el por-100 exacto.

  (dry-run)  python -m scripts.fix_ons_flujo_a_per100
  (aplica)   python -m scripts.fix_ons_flujo_a_per100 --commit

ALCANCE: sólo ONs con curva ^on, moneda_flujo=DL y flujo en escala peso
(Σ amort+interes > 500). Idempotente: si Σamort ya ≈ 100, factor=1, no toca.
Bumpea ingestado_en (watermark del sync SQL). Preserva la estructura (amort y
cupones se dividen por el mismo factor → mantienen su proporción).

REGLA #2: el factor asume que el flujo cargado amortiza el 100% del capital
(Σamort = VN). Para cupón-cero bullet es exacto. Revisá el dry-run antes de
aplicar: cada bono muestra Σamort, nº de pagos y el primer/último pago.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime

from pymongo import UpdateOne

from core.mongo import get_mongo_client

TARGET_VN = 100.0       # por 100 VN
UMBRAL_PESO = 500.0     # Σ amort+interes por encima de esto = escala peso
# Sólo los PAGOS estaban en escala peso. valor_residual es el nominal residual
# (ya por-100) → NO se divide (dividirlo rompía la paridad).
CAMPOS = ("amortizacion", "interes")


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="aplica la normalización")
    args = ap.parse_args()

    trading = get_mongo_client()["Trading"]
    ons = list(trading["Curvas"].find(
        {"curva": {"$regex": "^on"}, "moneda_flujo": "DL"},
        {"_id": 0, "ticker_corto": 1, "flujos": 1}))

    ops: list[UpdateOne] = []
    ahora = datetime.now(UTC)
    print(f"{'ticker':<9}{'#pagos':>7}{'Σamort_antes':>15}{'factor':>10}{'Σamort_desp':>14}")
    print("-" * 60)

    for o in sorted(ons, key=lambda x: x.get("ticker_corto", "")):
        tc = o.get("ticker_corto", "")
        flujos = o.get("flujos") or []
        s_amort = sum(_f(f.get("amortizacion")) for f in flujos)
        s_total = sum(_f(f.get("amortizacion")) + _f(f.get("interes")) for f in flujos)
        if s_total <= UMBRAL_PESO or s_amort <= 0:
            continue  # ya está en escala ~100 (o sin amort) → no tocar

        factor = s_amort / TARGET_VN
        nuevos = []
        for f in flujos:
            nf = dict(f)
            for c in CAMPOS:
                if c in nf and nf[c] is not None:
                    nf[c] = round(_f(nf[c]) / factor, 6)
            nuevos.append(nf)

        s_amort_desp = sum(_f(f.get("amortizacion")) for f in nuevos)
        print(f"{tc:<9}{len(flujos):>7}{s_amort:>15,.1f}{factor:>10,.2f}{s_amort_desp:>14,.2f}")
        ops.append(UpdateOne(
            {"ticker_corto": tc},
            {"$set": {"flujos": nuevos, "ingestado_en": ahora}}))

    print("\n" + "=" * 60)
    print(f"{len(ops)} ONs DL con flujo en escala peso → a normalizar a ~100.")
    if not ops:
        print("✅ Nada que normalizar.")
        return
    if not args.commit:
        print("\n(DRY-RUN — no se escribió nada. Revisá los Σamort_desp ≈ 100 y corré --commit.)")
        return

    res = trading["Curvas"].bulk_write(ops, ordered=False)
    print(f"\n✅ {res.modified_count} ONs normalizadas. Reiniciá motor_curvas:  "
          "sudo systemctl restart motor_curvas")


if __name__ == "__main__":
    main()
