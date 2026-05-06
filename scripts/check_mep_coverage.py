"""Reporta qué fechas de Valuaciones.AuM NO tienen cotización MEP disponible.

Usa la misma regla del backend (`api/services/_mep.py::get_mep_for_date`):
para cada fecha_snapshot, busca el último doc de Valuaciones.Dolar con
`timestamp <= end-of-day(fecha)` y `mep != null`. Si no hay → fecha
queda como missing.

Es read-only. Útil para saber qué fechas no se van a poder dolarizar
en la vista AUM (banner ⚠ SIN MEP) y decidir si backfillear el feed.

Uso:
    python -m scripts.check_mep_coverage
    python -m scripts.check_mep_coverage --verbose   # imprime TODAS las missing
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from datetime import datetime

from core.mongo import get_mongo_client_read


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true",
                    help="Imprime TODAS las fechas faltantes (default: las primeras 30).")
    args = ap.parse_args()

    c = get_mongo_client_read()
    aum = c["Valuaciones"]["AuM"]
    dolar = c["Valuaciones"]["Dolar"]

    # 1. Cargar todos los timestamps válidos de Dolar (mep != null) ordenados.
    timestamps = sorted(
        d["timestamp"]
        for d in dolar.find({"mep": {"$ne": None}}, {"_id": 0, "timestamp": 1})
        if isinstance(d.get("timestamp"), datetime)
    )
    if not timestamps:
        print("⚠  Valuaciones.Dolar no tiene NINGÚN doc con `mep` válido.")
        return

    print("Valuaciones.Dolar:")
    print(f"  ticks con MEP:      {len(timestamps)}")
    print(f"  primer timestamp:   {timestamps[0]}")
    print(f"  último timestamp:   {timestamps[-1]}")

    # 2. Distinct fecha_snapshot de AuM.
    fechas = sorted(set(aum.distinct("fecha_snapshot")))
    if not fechas:
        print("\nValuaciones.AuM vacío — nada que chequear.")
        return

    print("\nValuaciones.AuM:")
    print(f"  fechas distintas:   {len(fechas)}")
    print(f"  primera:            {fechas[0]}")
    print(f"  última:             {fechas[-1]}")

    # 3. Para cada fecha, bisect_right del end-of-day → si idx==0, no hay
    #    ningún timestamp <= esa fecha → missing.
    missing: list[str] = []
    for f in fechas:
        try:
            target = datetime.fromisoformat(f + "T23:59:59")
        except ValueError:
            missing.append(f"{f}  (formato inválido)")
            continue
        idx = bisect_right(timestamps, target)
        if idx == 0:
            missing.append(f)

    print("\nResumen MEP coverage:")
    print(f"  fechas con MEP:     {len(fechas) - len(missing)}")
    print(f"  fechas sin MEP:     {len(missing)}")
    if not missing:
        print("\n✅ Todas las fechas del AuM tienen cotización MEP disponible.")
        return

    print(f"\nFechas sin MEP ({len(missing)}):")
    show = missing if args.verbose else missing[:30]
    for f in show:
        print(f"  {f}")
    if not args.verbose and len(missing) > 30:
        print(f"  ... y {len(missing) - 30} más  (corré con --verbose para verlas)")

    # Heurística: si todas las missing son ANTES del primer timestamp, es
    # gap de "feed empezó tarde". Si hay missing intermedias, es gap del feed.
    primer_dolar = timestamps[0].strftime("%Y-%m-%d")
    todas_antes = all(f < primer_dolar for f in missing if not f.endswith("(formato inválido)"))
    if todas_antes:
        print(f"\nNota: todas las missing son ANTERIORES al primer tick del "
              f"feed ({primer_dolar}). Si querés cubrirlas, hay que backfillear "
              f"`Valuaciones.Dolar` con MEP histórico desde otra fuente.")
    else:
        intermedias = [f for f in missing if f >= primer_dolar]
        print(f"\nNota: {len(intermedias)} de las missing son POSTERIORES al "
              f"primer tick del feed ({primer_dolar}) — son gaps reales en "
              f"`Valuaciones.Dolar`. Revisar el script local que popula el feed.")


if __name__ == "__main__":
    main()
