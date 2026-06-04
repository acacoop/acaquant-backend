"""scripts/backfill_mep_operaciones.py — estampa `mep` (dólar de la fecha) en
cada doc de CashFlow.Operaciones, igual que el `mep` snapshot de NegocioMovimientos.

Mismo patrón que NegocioMovimientos: para cada `concertacion` se toma el último
MEP de Valuaciones.Dolar con timestamp <= fin de ese día (api.services._mep.
get_mep_for_date). Habilita dolarizar la vista MOVIMIENTOS (bruto/arancel ÷ mep).

EFICIENTE: una lookup de MEP por fecha DISTINTA (~cientos) + un updateMany por
fecha — NO 488k updates uno por uno. Idempotente (re-estampar es seguro).

REGLA #2 — no asume cobertura: el feed Valuaciones.Dolar puede no tener historia
tan vieja como Operaciones. El script REPORTA cuántas fechas/docs quedan sin MEP
(las fechas sin MEP NO se tocan → re-correr tras backfillear Dolar las completa;
el consumidor puede caer a get_mep_for_date en vivo igual que NegocioMov).

Read+write (get_mongo_client). Corré --dry-run primero. En nohup (tarda por las 488k):
    nohup venv/bin/python -m scripts.backfill_mep_operaciones > logs/backfill_mep.log 2>&1 &
    python -m scripts.backfill_mep_operaciones --dry-run   # solo reporta cobertura, no escribe
"""
from __future__ import annotations

import argparse

from api.services._mep import get_mep_for_date
from core.mongo import get_mongo_client


def run(dry: bool = False) -> dict:
    coll = get_mongo_client()["CashFlow"]["Operaciones"]

    # 1 agregación: {concertacion → # docs}. Evita 1 count por fecha.
    por_fecha: dict[str, int] = {
        r["_id"]: r["n"]
        for r in coll.aggregate([
            {"$match": {"concertacion": {"$ne": None}}},
            {"$group": {"_id": "$concertacion", "n": {"$sum": 1}}},
        ])
        if r.get("_id")
    }
    fechas = sorted(por_fecha)
    if not fechas:
        print("Sin docs con `concertacion`. Nada que hacer.")
        return {"fechas": 0}
    print(f"Fechas distintas: {len(fechas)} ({fechas[0]} … {fechas[-1]}) · "
          f"{sum(por_fecha.values()):,} docs con fecha" + ("  [DRY-RUN]" if dry else ""))

    fechas_con = fechas_sin = docs_estampados = docs_sin_mep = 0
    fechas_sin_mep: list[str] = []
    for f in fechas:
        mep = get_mep_for_date(f)
        if mep is None:
            fechas_sin += 1
            docs_sin_mep += por_fecha[f]
            fechas_sin_mep.append(f)
            continue
        fechas_con += 1
        if not dry:
            coll.update_many({"concertacion": f}, {"$set": {"mep": mep}})
        docs_estampados += por_fecha[f]

    print("\n── Resultado ──")
    print(f"  {'(estamparía)' if dry else 'estampados'}: {docs_estampados:,} docs "
          f"en {fechas_con} fechas con MEP")
    if fechas_sin:
        rango = f"{min(fechas_sin_mep)} … {max(fechas_sin_mep)}"
        print(f"  ⚠ SIN MEP: {docs_sin_mep:,} docs en {fechas_sin} fechas ({rango}) — "
              f"Valuaciones.Dolar no cubre esas fechas. NO se tocaron.")
    else:
        print("  ✓ Todas las fechas tienen MEP en Valuaciones.Dolar.")
    return {"fechas": len(fechas), "fechas_con_mep": fechas_con,
            "fechas_sin_mep": fechas_sin, "docs_estampados": docs_estampados,
            "docs_sin_mep": docs_sin_mep, "dry": dry}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="no escribe, solo reporta cobertura")
    args = ap.parse_args()
    res = run(dry=args.dry_run)
    print(f"\n→ {res}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
