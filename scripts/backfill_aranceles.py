"""backfill_aranceles.py — agrega `aranceles` a CashFlow.NegocioMovimientos.

NegocioMovimientos tiene todo el boleto MENOS el arancel (su fuente,
consolidadosGenerales, no lo trae). Este backfill toma las cuentas presentes
en NegocioMovimientos, pide a Aunesa `/operaciones/informes` por cuenta y
matchea por `boleto == comprobante` → `$set` del arancel en el doc del boleto.

Escribe en cada boleto:
    aranceles : {"ARS": 1102.80}     # dict por moneda (lo que trae informes)
    arancel   : 1102.80              # atajo: monto ARS (0.0 si no hay)

Idempotente (re-corrible). DRY-RUN por default. Empezá por una cuenta para
validar (ej. 1346, que sabemos que tiene compras con arancel).

Uso (en el Droplet, desde la raíz):
    python -m scripts.backfill_aranceles --cuenta 1346 --dry-run
    python -m scripts.backfill_aranceles --cuenta 1346 --apply
    python -m scripts.backfill_aranceles --desde 2026-01-01 --hasta 2026-05-26 --apply
    python -m scripts.backfill_aranceles --apply --limit 50
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

from pymongo import UpdateOne

from api.services.aunesa_informes import aranceles_por_boleto
from core.mongo import get_mongo_client

DB = "CashFlow"
COL = "NegocioMovimientos"

# Margen para la ventana de LIQUIDACIÓN: un boleto concertado en `hasta` liquida
# días después (T+1/T+2/...) → ampliamos el techo para no perder esos boletos.
_MARGEN_LIQ_DIAS = 15


def _ddmmyyyy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill de aranceles en NegocioMovimientos.")
    ap.add_argument("--cuenta", action="append", default=None,
                    help="restringir a esta(s) cuenta(s) (id). Repetible. Default: todas las de NM.")
    ap.add_argument("--desde", default=None, help="concertación desde YYYY-MM-DD (default -120d).")
    ap.add_argument("--hasta", default=None, help="concertación hasta YYYY-MM-DD (default hoy).")
    ap.add_argument("--limit", type=int, default=0, help="máximo de cuentas a procesar (0 = todas).")
    ap.add_argument("--apply", action="store_true", help="escribe. Sin esto, DRY-RUN.")
    args = ap.parse_args()

    hasta = date.fromisoformat(args.hasta) if args.hasta else date.today()
    desde = date.fromisoformat(args.desde) if args.desde else (hasta - timedelta(days=120))
    # Ventana de liquidación para informes (concertación + margen).
    liq_desde = _ddmmyyyy(desde)
    liq_hasta = _ddmmyyyy(hasta + timedelta(days=_MARGEN_LIQ_DIAS))

    col = get_mongo_client()[DB][COL]

    # Cuentas a procesar: las pasadas, o las que tienen boletos en el rango.
    if args.cuenta:
        cuentas = [str(c) for c in args.cuenta]
    else:
        cuentas = sorted(
            str(c) for c in col.distinct(
                "id_cuenta", {"fecha": {"$gte": desde.isoformat(), "$lte": hasta.isoformat()}},
            ) if c
        )
    if args.limit:
        cuentas = cuentas[: args.limit]

    print(f"Rango concertación {desde}..{hasta} | liquidación {liq_desde}..{liq_hasta}")
    print(f"Cuentas a procesar: {len(cuentas)} | modo: {'APPLY' if args.apply else 'DRY-RUN'}\n")

    tot_boletos_informes = 0
    tot_match = 0
    tot_sin_match = 0
    ops: list[UpdateOne] = []
    ejemplos: list[str] = []
    for i, cuenta in enumerate(cuentas, 1):
        try:
            mapa = aranceles_por_boleto(cuenta, liq_desde, liq_hasta)
        except Exception as e:
            print(f"   [{i}/{len(cuentas)}] cuenta {cuenta}: ERROR {e}")
            continue
        tot_boletos_informes += len(mapa)
        match_cuenta = 0
        for boleto, aranceles in mapa.items():
            arancel_ars = round(aranceles.get("ARS", 0.0), 2)
            res = col.find_one(
                {"comprobante": boleto, "id_cuenta": cuenta}, {"_id": 1},
            )
            if not res:
                tot_sin_match += 1
                continue
            match_cuenta += 1
            ops.append(UpdateOne(
                {"_id": res["_id"]},
                {"$set": {"aranceles": aranceles, "arancel": arancel_ars}},
            ))
            if len(ejemplos) < 5:
                ejemplos.append(f"{boleto} (cuenta {cuenta}) → aranceles={aranceles}")
        tot_match += match_cuenta
        if mapa:
            print(f"   [{i}/{len(cuentas)}] cuenta {cuenta}: {len(mapa)} boletos c/arancel, "
                  f"{match_cuenta} matchean en NM")

    print(f"\nResumen: {tot_boletos_informes} boletos con arancel en informes | "
          f"{tot_match} matchean en NM | {tot_sin_match} sin match (boleto no está en NM)")

    if not ops:
        print("Nada para escribir.")
        return 0
    if not args.apply:
        print(f"\n[DRY-RUN] {len(ops)} boletos se enriquecerían. Ejemplos:")
        for ej in ejemplos:
            print(f"   {ej}")
        print("Re-corré con --apply para escribir.")
        return 0

    res = col.bulk_write(ops, ordered=False)
    print(f"\n✅ Enriquecidos {res.modified_count} boletos con arancel.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
