"""backfill_cuenta_ops.py — trae la HISTORIA de boletos de UNA cuenta (one-shot).

Para cuentas recién incorporadas al universo de la ingesta (ej. la cuenta propia
1839): la corrida normal de `operaciones_informes` solo mira ~2 días de concertación
hacia atrás, así que la historia previa no entra. Este backfill consulta /informes
de UNA sola cuenta con una ventana amplia y la ingesta (idempotente — upsert por
boleto). Scopeado a 1 cuenta → liviano y seguro (REGLA #4).

Uso:
    python -m scripts.backfill_cuenta_ops 1839 --desde 2026-01-01
    python -m scripts.backfill_cuenta_ops 1839            # desde 1-ene del año
"""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, timedelta

from api.services import operaciones_informes as svc
from jobs import operaciones_informes as oi


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cuenta", help="id_cuenta (ej. 1839)")
    ap.add_argument("--desde", help="YYYY-MM-DD concertación desde (default: 1-ene del año)")
    ap.add_argument("--hasta", help="YYYY-MM-DD concertación hasta (default: hoy ART)")
    args = ap.parse_args()

    hoy = (datetime.now(UTC) - timedelta(hours=3)).date()  # ART
    desde_d = (datetime.strptime(args.desde, "%Y-%m-%d").date() if args.desde
               else date(hoy.year, 1, 1))
    hasta_d = (datetime.strptime(args.hasta, "%Y-%m-%d").date() if args.hasta else hoy)

    conc_desde, conc_hasta = oi._ddmmyyyy(desde_d), oi._ddmmyyyy(hasta_d)
    liq_desde = oi._ddmmyyyy(desde_d)
    liq_hasta = oi._ddmmyyyy(hasta_d + timedelta(days=oi._BUFFER_LIQ))

    print(f"Backfill cuenta {args.cuenta} · concertación {conc_desde}..{conc_hasta}")
    rows, ok = oi._fetch_cuenta(args.cuenta, conc_desde, conc_hasta, liq_desde, liq_hasta)
    if not ok:
        print("⚠ la consulta a Aunesa falló (timeout/error). Reintentá o achicá la ventana.")
        return
    print(f"  {len(rows)} boletos recibidos.")
    if not rows:
        print("  sin operaciones en esa ventana (¿cuenta/fechas correctas?).")
        return

    maps = svc.cargar_maps_enrich()
    res = svc.ingestar_filas_sql(rows, enrich_maps=maps)
    print(f"  ✓ {res['upsertadas']} nuevas / {res['modificadas']} actualizadas "
          f"en operaciones.operaciones")


if __name__ == "__main__":
    main()
