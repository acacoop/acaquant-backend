"""fix_curva_dup_plc5d.py — borra el doc duplicado MALO de PLC5D en Trading.Curvas.

One-shot scopeado (REGLA #4). El gate de renta fija + diag_curvas_duplicados detectaron que
`MERV - XMEV - PLC5D - 24hs` tiene 2 docs en Curvas: ticker_corto 'PLC5O' (letra O, código
correcto) y 'PLC50' (cero, ERRÓNEO — las ONs terminan en letra O). Esto borra SOLO el malo.

Dry-run por default. Idempotente (si ya no está, no hace nada).

    python -m scripts.fix_curva_dup_plc5d            # dry-run
    python -m scripts.fix_curva_dup_plc5d --apply    # borra

OJO sector: el doc que QUEDA ('PLC5O') tiene sector 'otros'. Si PLC5D es Pampa (energía),
corregí el sector desde Manager → TÍTULOS → ONs (segmentar) — NO lo toca este script.
Después: `python -m jobs.sync_postgres` (saca 'PLC50' de mercado.curvas) + re-correr el gate.
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client

_TICKER = "MERV - XMEV - PLC5D - 24hs"
_MALO = "PLC50"   # ticker_corto erróneo (cero)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="borrar (default: dry-run)")
    args = ap.parse_args()

    col = get_mongo_client()["Trading"]["Curvas"]
    filtro = {"ticker": _TICKER, "ticker_corto": _MALO}
    n = col.count_documents(filtro)
    print(f"Docs que matchean (ticker={_TICKER!r}, ticker_corto={_MALO!r}): {n}")

    # Salvaguarda: confirmar que el BUENO ('PLC5O') existe antes de borrar el malo,
    # para no dejar el instrumento sin ningún doc.
    bueno = col.count_documents({"ticker": _TICKER, "ticker_corto": "PLC5O"})
    print(f"Doc bueno (ticker_corto='PLC5O') presente: {bueno}")
    if n and not bueno:
        print("⚠️  ABORTO: no encuentro el doc bueno 'PLC5O' — no borro para no dejarlo sin doc.")
        return 1

    if not args.apply:
        print("\n(DRY-RUN — nada borrado. Correr con --apply.)")
        return 0

    res = col.delete_one(filtro)
    print(f"\n✅ Borrados: {res.deleted_count}. Queda solo 'PLC5O'.")
    print("Siguiente: python -m jobs.sync_postgres  +  re-correr el gate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
