"""diag_cedears_sin_historia.py — qué CEDEARs no tienen historia en SQL (para backfillear).

Read-only. Por cada CEDEAR de mercado.cedears, chequea si su SUBYACENTE (underlying) tiene:
  - EOD en mercado.precios_acciones (para retornos/MTD/YTD/pivots), y
  - snapshot live en mercado.adr_snapshot.
Lista los que faltan → esos necesitan estar en el universo de jobs/precios_acciones_daily +
adr_live y backfillearse (sync_postgres --full o --days N). Útil para CEDEARs/ADR nuevos.

    python -m scripts.diag_cedears_sin_historia
"""
from __future__ import annotations

from core.postgres import get_pool


def main() -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker_corto, upper(COALESCE(underlying, ticker_corto)) AS u, "
                    "       activo, rubro FROM mercado.cedears ORDER BY ticker_corto")
        cedears = cur.fetchall()

        cur.execute("SELECT DISTINCT upper(ticker) FROM mercado.precios_acciones")
        con_eod = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT DISTINCT upper(ticker) FROM mercado.adr_snapshot")
        con_adr = {r[0] for r in cur.fetchall()}

    sin_eod, sin_adr, sin_rubro = [], [], []
    for corto, u, activo, rubro in cedears:
        if u not in con_eod:
            sin_eod.append(f"{corto}({u})")
        if u not in con_adr:
            sin_adr.append(f"{corto}({u})")
        if not rubro:
            sin_rubro.append(corto)

    print(f"CEDEARs totales: {len(cedears)}")
    print(f"\nSIN EOD (mercado.precios_acciones) — {len(sin_eod)}:")
    print(f"  {sin_eod or 'ninguno ✅'}")
    print(f"\nSIN ADR snapshot (mercado.adr_snapshot) — {len(sin_adr)}:")
    print(f"  {sin_adr or 'ninguno ✅'}")
    print(f"\nSIN RUBRO (no estaban en el CSV) — {len(sin_rubro)}:")
    print(f"  {sin_rubro or 'ninguno ✅'}")
    print("\nLos SIN EOD/ADR: agregar el subyacente al universo de jobs/precios_acciones_daily "
          "+ jobs/adr_live y correr el fetch/backfill. Los SIN RUBRO: clasificar en "
          "Manager → TÍTULOS → RENTA VARIABLE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
