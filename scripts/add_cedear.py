"""scripts/add_cedear.py — alta/edición de un asset en el maestro Trading.Cedears.

UN doc en `Trading.Cedears` (activo=True) cablea TODO el universo de renta variable:
  - `engines/motor_cedears` lo trackea LIVE (scanner CEDEAR) — usa `ticker` (BYMA) + `ticker_corto`.
  - `jobs/adr_live` trae el ADR del `underlying` (símbolo US) cada 15'.
  - `jobs/precios_acciones_daily` trae los precios EOD (Yahoo) del underlying.
  - el Scanner (`/renta-variable`) lo muestra.

`ratio_cedear` es SOLO display (el scanner lo pasa al front). NO afecta el tracking.
Sin `--ticker` → `sin_cedear=True` (ETF/ADR US sin cedear BYMA; columnas CEDEAR vacías).

Idempotente: upsert por `ticker_corto`. Dry-run por default; aplica con --apply.

    python -m scripts.add_cedear --ticker-corto SPCX --underlying SPCX \
        --ticker "MERV - XMEV - SPCX - 24hs" --nombre SPCX            # dry-run
    python -m scripts.add_cedear ... --apply                          # aplica

Tras el alta CON cedear: reiniciar motor_cedears.service (lee el master al arrancar).
"""
import argparse

from core.mongo import get_mongo_client


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker-corto", required=True, help="ej. SPCX")
    ap.add_argument("--underlying", default=None, help="símbolo US (default = ticker-corto)")
    ap.add_argument("--ticker", default=None,
                    help="ticker BYMA del cedear (ej. 'MERV - XMEV - SPCX - 24hs'). "
                         "Si NO se pasa → sin_cedear=True (ADR/ETF only).")
    ap.add_argument("--nombre", default=None)
    ap.add_argument("--sector", default=None)
    ap.add_argument("--ratio", type=float, default=None, help="ratio CEDEAR:acción (solo display)")
    ap.add_argument("--apply", action="store_true", help="aplica (sin esto = dry-run)")
    args = ap.parse_args()

    tc = args.ticker_corto.upper()
    sin_cedear = not args.ticker
    doc = {
        "ticker":        args.ticker or tc,
        "ticker_corto":  tc,
        "underlying":    (args.underlying or tc).upper(),
        "nombre":        args.nombre or tc,
        "sector":        args.sector,
        "ratio_cedear":  args.ratio,
        "sin_cedear":    sin_cedear,
        "activo":        True,
    }

    col = get_mongo_client()["Trading"]["Cedears"]
    existe = col.find_one({"ticker_corto": tc}, {"_id": 0, "ticker_corto": 1})
    print(f"{'APLICA' if args.apply else 'DRY-RUN'} — upsert Trading.Cedears ticker_corto={tc}")
    print(f"  ya existe: {'sí' if existe else 'no'}  ·  sin_cedear: {sin_cedear}")
    for k, v in doc.items():
        print(f"    {k:<14} {v!r}")

    if not args.apply:
        print("\n(dry-run) Revisá el doc y re-corré con --apply.")
        return 0

    res = col.update_one({"ticker_corto": tc}, {"$set": doc}, upsert=True)
    print(f"\n✅ matched={res.matched_count} upserted={'sí' if res.upserted_id else 'no'}")
    print("Próxima corrida de precios_acciones_daily / adr_live + el motor ya lo incluyen.")
    if not sin_cedear:
        print("⚠️ CON cedear → reiniciá motor_cedears.service para que suscriba el ticker nuevo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
