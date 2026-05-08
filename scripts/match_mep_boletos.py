"""match_mep_boletos.py — completa el campo `mep` en
CashFlow.NegocioMovimientos para boletos que quedaron con `mep: null`.

Caso típico: backfills históricos (2023, 2024) hechos cuando
Valuaciones.Dolar no tenía cotización para esas fechas. Una vez que
se cargó el MEP histórico, este script "rellena" los boletos sin
MEP con el MEP del día correspondiente.

Algoritmo:
  1) Agrupar boletos sin MEP por fecha.
  2) Para cada fecha: lookup get_mep_for_date(fecha).
  3) Si existe MEP → update_many sobre esa fecha.
     Si no existe → skip y reportar (fecha sin cotización aún).

Idempotente. Solo toca docs con mep null o sin field.

Uso:
    python -m scripts.match_mep_boletos                      # ejecuta
    python -m scripts.match_mep_boletos --dry                # solo reporta
    python -m scripts.match_mep_boletos --desde 2024-01-01 --hasta 2024-12-31
"""
from __future__ import annotations

import argparse

from api.services._mep import get_mep_for_date
from core.mongo import get_mongo_client


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry", action="store_true",
                        help="Solo reporta, no escribe.")
    parser.add_argument("--desde", default=None,
                        help="YYYY-MM-DD inclusive (filtro opcional).")
    parser.add_argument("--hasta", default=None,
                        help="YYYY-MM-DD inclusive (filtro opcional).")
    args = parser.parse_args()

    coll = get_mongo_client()["CashFlow"]["NegocioMovimientos"]

    # Match base: docs sin mep (null o sin field).
    base_match: dict = {
        "$or": [{"mep": None}, {"mep": {"$exists": False}}],
    }
    if args.desde or args.hasta:
        rango: dict[str, str] = {}
        if args.desde:
            rango["$gte"] = args.desde
        if args.hasta:
            rango["$lte"] = args.hasta
        base_match["fecha"] = rango

    # 1) Pipeline: fechas distintas con docs sin MEP.
    pipeline = [
        {"$match": base_match},
        {"$group": {"_id": "$fecha", "n": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]
    fechas = list(coll.aggregate(pipeline))
    total_docs = sum(f["n"] for f in fechas)
    print(f"Fechas con boletos sin MEP: {len(fechas)} · total docs: {total_docs}")
    if not fechas:
        return 0

    # 2) Por cada fecha: lookup + update_many.
    n_actualizados = 0
    n_sin_mep = 0
    fechas_sin_mep: list[str] = []
    n_dry_match = 0

    for f in fechas:
        fecha = f["_id"]
        n_docs = f["n"]
        if not isinstance(fecha, str):
            continue

        mep = get_mep_for_date(fecha)
        if mep is None or mep <= 0:
            n_sin_mep += n_docs
            fechas_sin_mep.append(fecha)
            continue

        if args.dry:
            n_dry_match += n_docs
            continue

        # Mismo filtro que el aggregate, scope a esta fecha.
        match = dict(base_match)
        match["fecha"] = fecha
        result = coll.update_many(match, {"$set": {"mep": mep}})
        n_actualizados += result.modified_count

    if args.dry:
        print(f"\n[DRY] {n_dry_match} docs serían actualizados.")
        if fechas_sin_mep:
            print(f"      {n_sin_mep} docs en {len(fechas_sin_mep)} fechas sin MEP "
                  f"(no se actualizarían).")
            print(f"      Primeras fechas sin MEP: {fechas_sin_mep[:10]}")
        return 0

    print(f"\nActualizados: {n_actualizados} docs.")
    if fechas_sin_mep:
        print(f"Pendientes (sin MEP en Valuaciones.Dolar): {n_sin_mep} docs en "
              f"{len(fechas_sin_mep)} fechas.")
        print(f"Primeras: {fechas_sin_mep[:10]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
