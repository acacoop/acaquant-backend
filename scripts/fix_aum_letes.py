"""fix_aum_letes.py — backfill: recalcula valuacion de docs con
tipoTitulo='LETES' que quedaron como P×Q en lugar de P×Q/100.

Bug original (2026-05-14): el set TIPOS_DIVISOR_100 en jobs/aum.py NO
incluía "LETES". Aunesa categorizó algunas ON (ej. DHSFO ON CREDICUOT)
como LETES, y el cron AuM las valuó P×Q sin dividir — overcounting
×100 en el AuM total.

Este script:
  1. Recorre Valuaciones.AuM con tipoTitulo='LETES'.
  2. Detecta los que tienen `valuacion ≈ cantidad × precio` (sin /100).
     Los que ya están como P×Q/100 (ratio ≈ 0.01) NO se tocan —
     idempotente.
  3. Actualiza valuacion = round(P × Q / 100, 6).

Uso:
    python -m scripts.fix_aum_letes --dry-run   # solo lista qué tocaría
    python -m scripts.fix_aum_letes             # update real
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client


# Si valuacion / (P × Q) está cerca de 1 (entre 0.5 y 2) → no dividió.
# Si está cerca de 0.01 (entre 0.005 y 0.02) → ya está bien (no tocar).
# Cualquier otra cosa → raro, log y skip por seguridad.
RATIO_SIN_DIVIDIR_LO = 0.5
RATIO_SIN_DIVIDIR_HI = 2.0


def run(dry_run: bool = False) -> None:
    print("=" * 80)
    print(f"FIX Valuaciones.AuM — tipoTitulo='LETES' sin /100")
    print(f"Modo: {'DRY-RUN' if dry_run else 'UPDATE REAL'}")
    print("=" * 80)

    db = get_mongo_client()["Valuaciones"]
    col = db["AuM"]

    docs = list(col.find(
        {"tipoTitulo": "LETES"},
        {"_id": 1, "fecha_snapshot": 1, "id_cuenta": 1,
         "cantidad": 1, "precio": 1, "valuacion": 1},
    ))
    print(f"\nDocs con tipoTitulo='LETES': {len(docs)}")

    a_corregir = []
    ya_ok = 0
    raros = 0
    for d in docs:
        cant = float(d.get("cantidad") or 0)
        precio = float(d.get("precio") or 0)
        valuac = float(d.get("valuacion") or 0)
        pq = cant * precio
        if pq == 0:
            raros += 1
            continue
        ratio = valuac / pq
        if RATIO_SIN_DIVIDIR_LO <= ratio <= RATIO_SIN_DIVIDIR_HI:
            # No dividía — corregir.
            a_corregir.append({
                "_id":           d["_id"],
                "fecha":         d.get("fecha_snapshot"),
                "cuenta":        d.get("id_cuenta"),
                "valuac_actual": valuac,
                "valuac_nueva":  round(pq / 100, 6),
            })
        elif 0.005 <= ratio <= 0.02:
            ya_ok += 1  # ya estaba como P×Q/100
        else:
            raros += 1
            print(f"  ⚠ raro: ratio={ratio:.4f}  cuenta={d.get('id_cuenta')}  "
                  f"fecha={str(d.get('fecha_snapshot',''))[:10]}  valuac={valuac}")

    print(f"\nResumen:")
    print(f"  A corregir (sin /100):   {len(a_corregir)}")
    print(f"  Ya OK (con /100):        {ya_ok}")
    print(f"  Raros (skip):            {raros}")

    if not a_corregir:
        print("\nNada que corregir.")
        return

    print(f"\nPrimeros 10 a corregir:")
    print(f"  {'FECHA':<12} {'CTA':<6} {'ACTUAL':>20} {'NUEVA':>20}")
    for d in a_corregir[:10]:
        print(f"  {str(d['fecha'])[:10]:<12} {str(d['cuenta'])[:6]:<6} "
              f"{d['valuac_actual']:>20,.2f} {d['valuac_nueva']:>20,.2f}")
    if len(a_corregir) > 10:
        print(f"  ... y {len(a_corregir) - 10} más")

    if dry_run:
        print("\n[DRY-RUN] Nada se escribió. Re-correr sin --dry-run para aplicar.")
        return

    print(f"\nAplicando {len(a_corregir)} updates...")
    bulk = [
        {"_id": d["_id"], "valuac_nueva": d["valuac_nueva"]}
        for d in a_corregir
    ]
    n_ok = 0
    for op in bulk:
        col.update_one(
            {"_id": op["_id"]},
            {"$set": {"valuacion": op["valuac_nueva"]}},
        )
        n_ok += 1
    print(f"\n✓ {n_ok} docs actualizados.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(dry_run=args.dry_run)
