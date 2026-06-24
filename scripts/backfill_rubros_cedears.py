"""backfill_rubros_cedears.py — carga rubro + es_ia en mercado.cedears desde el CSV.

Fuente: docs/cedears_clasificado_final.csv (ticker US, nombre, rubro, es_ia SI/NO).
- Llena el catálogo mercado.rubros (rubros distintos; es_ia_def = mayoría del rubro).
- Setea mercado.cedears.rubro/es_ia por CEDEAR matcheando CSV.ticker (US) == underlying
  (COALESCE underlying, ticker_corto). Los CEDEARs que NO están en el CSV quedan con rubro
  NULL (vacío, NO se borran — pedido del user).

Idempotente. Dry-run por default (REGLA #4).
    python -m scripts.backfill_rubros_cedears            # cuenta (dry-run)
    python -m scripts.backfill_rubros_cedears --apply    # escribe
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from core.postgres import get_pool

_CSV = Path(__file__).resolve().parent.parent / "docs" / "cedears_clasificado_final.csv"


def _leer_csv() -> list[dict]:
    with _CSV.open(encoding="utf-8") as f:
        return [
            {"ticker": (r["ticker"] or "").strip().upper(),
             "nombre": (r.get("nombre") or "").strip(),
             "rubro": (r["rubro"] or "").strip(),
             "es_ia": (r.get("es_ia") or "").strip().upper() == "SI"}
            for r in csv.DictReader(f) if (r.get("ticker") or "").strip()
        ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="escribir (default: dry-run)")
    args = ap.parse_args()

    filas = _leer_csv()
    print(f"CSV: {len(filas)} empresas")

    # Catálogo de rubros: es_ia_def = mayoría (SI > NO) de las empresas del rubro.
    por_rubro: dict[str, Counter] = {}
    for r in filas:
        if r["rubro"]:
            por_rubro.setdefault(r["rubro"], Counter())[r["es_ia"]] += 1
    rubros = {rub: (c[True] > c[False]) for rub, c in por_rubro.items()}
    print(f"Rubros distintos: {len(rubros)} → {sorted(rubros)}")

    with get_pool().connection() as conn, conn.cursor() as cur:
        # Match CSV.ticker (US) contra los CEDEARs por underlying/ticker_corto.
        matched, sin_match = 0, []
        for r in filas:
            cur.execute(
                "SELECT count(*) FROM mercado.cedears "
                "WHERE upper(COALESCE(underlying, ticker_corto)) = %s", (r["ticker"],))
            if cur.fetchone()[0] > 0:
                matched += 1
            else:
                sin_match.append(r["ticker"])
        cur.execute("SELECT count(*) FROM mercado.cedears")
        n_ced = cur.fetchone()[0]

        print(f"\nCEDEARs en SQL: {n_ced}")
        print(f"Empresas del CSV que matchean un CEDEAR: {matched}/{len(filas)}")
        if sin_match:
            print(f"  CSV sin CEDEAR ({len(sin_match)}): {sin_match[:20]}")
        print(f"CEDEARs que quedarían SIN rubro (no están en el CSV): "
              f"~{n_ced - matched} (quedan vacíos, no se borran)")

        if not args.apply:
            print("\n(DRY-RUN — nada escrito. Correr con --apply.)")
            return 0

        # 1) catálogo
        for rub, def_ia in rubros.items():
            cur.execute(
                "INSERT INTO mercado.rubros (rubro, es_ia_def) VALUES (%s, %s) "
                "ON CONFLICT (rubro) DO UPDATE SET es_ia_def = EXCLUDED.es_ia_def",
                (rub, def_ia))
        # 2) por CEDEAR (match por underlying/ticker_corto)
        n_upd = 0
        for r in filas:
            cur.execute(
                "UPDATE mercado.cedears SET rubro = %s, es_ia = %s "
                "WHERE upper(COALESCE(underlying, ticker_corto)) = %s",
                (r["rubro"] or None, r["es_ia"], r["ticker"]))
            n_upd += cur.rowcount
        conn.commit()
        print(f"\n✅ Rubros catálogo: {len(rubros)}. CEDEARs actualizados: {n_upd}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
