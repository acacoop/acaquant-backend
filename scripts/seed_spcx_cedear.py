"""scripts/seed_spcx_cedear.py — alta de SPCX (CON cedear) en el maestro Trading.Cedears.

Un doc activo en Trading.Cedears cablea TODO: motor_cedears (CEDEAR live), adr_live
(ADR del underlying), precios_acciones_daily (precios EOD) y el Scanner (/renta-variable).

⚠️ VERIFICÁ el `ticker` BYMA en el dry-run: si el cedear de SPCX no es exactamente
`MERV - XMEV - SPCX - 24hs`, editá la constante DOC abajo antes de aplicar (es lo que
el motor suscribe vía pyRofex — si está mal, no llega data del CEDEAR).

Idempotente: upsert por ticker_corto. Dry-run por default; aplica con --apply.

    python -m scripts.seed_spcx_cedear            # dry-run (muestra el doc, no escribe)
    python -m scripts.seed_spcx_cedear --apply    # aplica el upsert

Tras aplicar: reiniciá motor_cedears.service (lee el master al arrancar).
"""
import argparse

from core.mongo import get_mongo_client

DOC = {
    "ticker":        "MERV - XMEV - SPCX - 24hs",  # ← VERIFICAR el ticker BYMA real
    "ticker_corto":  "SPCX",
    "underlying":    "SPCX",   # símbolo US (PreciosAcciones / AdrSnapshot)
    "nombre":        "SPCX",
    "sector":        None,
    "ratio_cedear":  None,     # solo display, opcional
    "sin_cedear":    False,    # SÍ tiene cedear en BYMA
    "activo":        True,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="aplica (sin esto = dry-run)")
    args = ap.parse_args()

    col = get_mongo_client()["Trading"]["Cedears"]
    existe = col.find_one({"ticker_corto": "SPCX"}, {"_id": 0, "ticker_corto": 1})
    print(f"{'APLICA' if args.apply else 'DRY-RUN'} — upsert Trading.Cedears ticker_corto=SPCX")
    print(f"  ya existe: {'sí' if existe else 'no'}")
    for k, v in DOC.items():
        print(f"    {k:<14} {v!r}")

    if not args.apply:
        print("\n(dry-run) Revisá el `ticker` BYMA y re-corré con --apply.")
        return 0

    res = col.update_one({"ticker_corto": "SPCX"}, {"$set": DOC}, upsert=True)
    print(f"\n✅ matched={res.matched_count} upserted={'sí' if res.upserted_id else 'no'}")
    print("Reiniciá motor_cedears.service para suscribir el ticker. adr_live (15') y")
    print("precios_acciones_daily ya lo incluyen en su próxima corrida.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
