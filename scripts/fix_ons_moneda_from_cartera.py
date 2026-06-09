"""scripts/fix_ons_moneda_from_cartera.py — sincroniza moneda_flujo ← CARTERA.

La fuente de verdad del TIPO de cada ON es `Valuaciones.Assets.CARTERA` (HD/DL),
que mantenés vos. El motor (engines/curvas.py) valúa según `Curvas.moneda_flujo`.
Cuando los dos no coinciden, la TEA sale mal. Este script alinea moneda_flujo a
lo que dice CARTERA:

    CARTERA = HD  → moneda_flujo = USD   (precio ÷MEP / as-is, flujo USD ~100)
    CARTERA = DL  → moneda_flujo = DL    (precio ÷A3500, flujo USD ~100)

NO toca los flujos. Sólo cambia la moneda. Para los DL cuyo flujo está en escala
PESO (Σ >> 200), avisa que ADEMÁS van a necesitar normalizar el flujo a ~100
(paso aparte) — con sólo cambiar la moneda esos quedan sin TEA hasta arreglar eso.

  (dry-run)  python -m scripts.fix_ons_moneda_from_cartera
  (aplica)   python -m scripts.fix_ons_moneda_from_cartera --commit

Idempotente. Bumpea `ingestado_en` en los modificados (watermark del sync SQL).
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime

from pymongo import UpdateOne

from core.mongo import get_mongo_client

# CARTERA → moneda_flujo que espera el motor.
CARTERA_A_MONEDA = {"HD": "USD", "DL": "DL"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="aplica los cambios")
    args = ap.parse_args()

    cli = get_mongo_client()
    trading = cli["Trading"]
    val = cli["Valuaciones"]

    # CARTERA por TICKER (uppercase).
    cartera: dict[str, str] = {}
    for a in val["Assets"].find(
            {"TICKER": {"$exists": True}}, {"_id": 0, "TICKER": 1, "CARTERA": 1}):
        t = (a.get("TICKER") or "").upper()
        if t:
            cartera[t] = (a.get("CARTERA") or "").upper()

    ons = list(trading["Curvas"].find(
        {"curva": {"$regex": "^on"}},
        {"_id": 0, "ticker_corto": 1, "moneda_flujo": 1, "valor_nominal": 1, "flujos": 1}))

    ops: list[UpdateOne] = []
    pend_flujo: list[str] = []
    ahora = datetime.now(UTC)

    print(f"{'ticker':<9}{'CARTERA':<9}{'moneda':<8}{'→ nueva':<9}  nota")
    print("-" * 60)
    for o in sorted(ons, key=lambda x: x.get("ticker_corto", "")):
        tc = (o.get("ticker_corto") or "").upper()
        cart = cartera.get(tc, "")
        destino = CARTERA_A_MONEDA.get(cart)
        actual = (o.get("moneda_flujo") or "USD").upper()
        if not destino or destino == actual:
            continue

        # ¿flujo en escala peso? (Σ amort+interes >> nominal) → necesita paso 2.
        vn = float(o.get("valor_nominal") or 100)
        sflujo = sum(float(f.get("amortizacion") or 0) + float(f.get("interes") or 0)
                     for f in (o.get("flujos") or []))
        peso_escala = destino == "DL" and sflujo > vn * 5
        nota = "⚠ flujo en PESO → además normalizar a ~100 (paso 2)" if peso_escala else "OK"
        if peso_escala:
            pend_flujo.append(tc)

        print(f"{tc:<9}{cart:<9}{actual:<8}{destino:<9}  {nota}")
        ops.append(UpdateOne(
            {"ticker_corto": tc},
            {"$set": {"moneda_flujo": destino, "ingestado_en": ahora}}))

    print("\n" + "=" * 60)
    print(f"{len(ops)} ONs a re-asignar moneda.")
    if pend_flujo:
        print(f"\n{len(pend_flujo)} de esos son DL con flujo en escala PESO → con sólo")
        print("cambiar la moneda quedan SIN TEA hasta normalizar el flujo a ~100:")
        print("  " + ", ".join(pend_flujo))

    if not ops:
        print("\n✅ Nada que cambiar — moneda_flujo ya coincide con CARTERA.")
        return

    if not args.commit:
        print("\n(DRY-RUN — no se escribió nada. Corré con --commit para aplicar.)")
        return

    res = trading["Curvas"].bulk_write(ops, ordered=False)
    print(f"\n✅ {res.modified_count} ONs actualizadas. Reiniciá motor_curvas para que tome "
          "la nueva moneda:  sudo systemctl restart motor_curvas")


if __name__ == "__main__":
    main()
