"""scripts/backfill_gated_decomiso.py — backfill Mongo→SQL de las 3 colecciones
GATEADAS del decomiso (las que tienen tabla SQL pero VACÍA porque su writer ya es
SQL-native y dejó la historia Mongo congelada).

Sin esto se pierde historia al dropear:
  - Trading.FitParams        → mercado.fit_params           (β de la cuadrática/cierre)
  - Trading.FairValueResiduos→ mercado.fair_value_residuos  (residuos + z; el z_temporal
                                necesita la ventana de 30 ruedas → sin backfill quedan
                                ~20 ruedas con z_temporal NULL)
  - Trading.OnsIgnoradas     → mercado.ons_ignoradas        (set "no es ON" del conciliador)

Shapes VERIFICADOS contra el writer pre-cutover (commit 56cbbf4^): los docs Mongo
usan EXACTAMENTE los mismos nombres de campo que las columnas SQL; lo único a convertir
es `ts_cierre` (Mongo: string 'YYYY-MM-DD' → SQL: date). Upsert idempotente por PK
(write_native), batcheado + sleep (REGLA #4). Read-only sobre Mongo.

    python -m scripts.backfill_gated_decomiso            # dry-run (cuenta, no escribe)
    python -m scripts.backfill_gated_decomiso --apply    # escribe SQL

Idempotente: re-correrlo no duplica (PK upsert). Fuera de rueda. Borrar del repo cuando
las 3 colecciones estén dropeadas. REGLA #0/#4/#5.
"""
from __future__ import annotations

import sys
import time
from datetime import date

from core.mongo import get_mongo_client_read
from core.pg_mirror import write_native

BATCH = 2000          # filas por lote (FairValueResiduos es la única grande)
SLEEP_S = 0.3         # respiro entre lotes para no starvar (REGLA #4)

# Columnas SQL por tabla (== keys del doc Mongo, salvo conversión de ts_cierre).
_FIT_COLS = [
    "ts_cierre", "curva", "updated_at", "beta0", "beta1", "beta2", "r2",
    "n_bonos_universo", "vol_min_aplicado", "sigma_dia_bps", "media_residuos_universo_bps",
]
_RES_COLS = [
    "ts_cierre", "curva", "ticker", "ticker_corto", "duration", "tea_obs",
    "tea_teorica", "residuo_bps", "z_estatico", "z_temporal", "n_obs", "en_universo",
]


def _to_date(v):
    """Mongo: string 'YYYY-MM-DD' (a veces datetime). SQL: date."""
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        return date.fromisoformat(v[:10])
    return getattr(v, "date", lambda: v)()  # datetime → date


def _row(doc: dict, cols: list[str]) -> dict:
    """Proyecta el doc Mongo a las columnas SQL (dropea _id, convierte ts_cierre)."""
    r = {c: doc.get(c) for c in cols}
    if "ts_cierre" in r and r["ts_cierre"] is not None:
        r["ts_cierre"] = _to_date(r["ts_cierre"])
    return r


def _backfill(db, coll: str, table: str, key_cols: list[str], cols: list[str],
              apply: bool) -> int:
    total = db[coll].estimated_document_count()
    print(f"\n{coll:<22} → {table:<28} (~{total:,} docs Mongo)")
    if not apply:
        print("   dry-run: no escribe.")
        return total
    escritas, buf = 0, []
    for doc in db[coll].find({}, projection={"_id": 0}):
        buf.append(_row(doc, cols))
        if len(buf) >= BATCH:
            escritas += write_native(table, key_cols, buf)
            print(f"   +{len(buf)} (acum {escritas:,})")
            buf = []
            time.sleep(SLEEP_S)
    if buf:
        escritas += write_native(table, key_cols, buf)
    print(f"   ✅ {escritas:,} filas upserteadas en {table}")
    return escritas


def main() -> int:
    apply = "--apply" in sys.argv
    cli = get_mongo_client_read()
    db = cli["Trading"]
    modo = "APPLY (escribe SQL)" if apply else "DRY-RUN (no escribe)"
    print(f"backfill_gated_decomiso — modo {modo}")

    _backfill(db, "FitParams", "mercado.fit_params",
              ["curva", "ts_cierre"], _FIT_COLS, apply)
    _backfill(db, "FairValueResiduos", "mercado.fair_value_residuos",
              ["curva", "ticker", "ts_cierre"], _RES_COLS, apply)
    _backfill(db, "OnsIgnoradas", "mercado.ons_ignoradas",
              ["ticker"], ["ticker", "ignorado_por", "at"], apply)

    print("\nListo." if apply else "\nDRY-RUN. Re-correr con --apply para escribir.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
