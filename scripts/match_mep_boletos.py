"""Completa el campo `mep` en `CashFlow.NegocioMovimientos`.

Para cada doc con `mep=null` (o todos si pasás `--refrescar`), busca la
cotización MEP en `Valuaciones.Dolar` para su fecha y la guarda. Útil
para completar boletos viejos cuya fecha no tenía cotización al momento
del backfill (porque el feed empezó tarde) y ahora sí, después de que
agregaras data manualmente.

Idempotente. Imprime solo conteos por fecha — sin importes individuales.

Uso:
    python -m scripts.match_mep_boletos --dry-run    # preview
    python -m scripts.match_mep_boletos              # solo los que tienen mep=null
    python -m scripts.match_mep_boletos --refrescar  # todos los boletos
"""
from __future__ import annotations

import argparse

from api.services._mep import get_mep_for_date
from core.mongo import get_mongo_client


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="No modifica nada — solo cuenta cuántos docs cambiarían.")
    ap.add_argument("--refrescar", action="store_true",
                    help="Pisa el mep de TODOS los boletos, no solo los null. "
                         "Útil si Valuaciones.Dolar tiene datos corregidos.")
    args = ap.parse_args()

    client = get_mongo_client()
    col = client["CashFlow"]["NegocioMovimientos"]

    if args.refrescar:
        base_filter: dict = {}
        modo = "TODOS los boletos"
    else:
        base_filter = {"$or": [{"mep": None}, {"mep": {"$exists": False}}]}
        modo = "boletos con mep=null"
    print(f"Modo: {modo}")

    fechas = sorted(f for f in col.distinct("fecha", base_filter) if isinstance(f, str))
    print(f"Fechas con docs a procesar: {len(fechas)}")
    if not fechas:
        print("Nada que actualizar.")
        return

    actualizadas = 0
    encontradas = 0
    sin_mep: list[str] = []

    for fecha in fechas:
        mep = get_mep_for_date(fecha)
        if mep is None:
            sin_mep.append(fecha)
            continue
        encontradas += 1
        f_match = {**base_filter, "fecha": fecha}
        if args.dry_run:
            actualizadas += col.count_documents(f_match)
        else:
            result = col.update_many(f_match, {"$set": {"mep": mep}})
            actualizadas += result.modified_count

    print(f"\nFechas con MEP encontrado:    {encontradas} / {len(fechas)}")
    print(f"Docs {'que se actualizarían' if args.dry_run else 'actualizados'}: {actualizadas}")
    print(f"Fechas SIN MEP en Valuaciones.Dolar: {len(sin_mep)}")

    if sin_mep:
        print("\nMuestra de fechas sin MEP (primeras 30):")
        for f in sin_mep[:30]:
            print(f"  {f}")
        if len(sin_mep) > 30:
            print(f"  ... y {len(sin_mep) - 30} más")
        print("\nEsas fechas necesitan data en Valuaciones.Dolar antes de "
              "que el script les pueda completar el mep.")

    if args.dry_run:
        print("\n--dry-run: nada se escribió.")


if __name__ == "__main__":
    main()
