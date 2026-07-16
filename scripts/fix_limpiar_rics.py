"""fix_limpiar_rics.py — borra los RICs auto-cargados por el feed (one-shot).

El arranque de eikon_feed persistió RICs resueltos por symbology que quedaron
como el ticker pelado (ABT → 'ABT', KO → 'KO'…). Este script los limpia:
borra SOLO los ric idénticos al underlying (los auto-cargados) y deja intactos
los con formato real (RKLB.O y lo que cargues a mano en Manager).

    python -m scripts.fix_limpiar_rics            # muestra qué borraría (dry-run)
    python -m scripts.fix_limpiar_rics --apply    # borra en serio

Se borra al cerrar el tema (REGLA #5).
"""
from __future__ import annotations

import argparse

from core.postgres import get_pool

WHERE = ("ric IS NOT NULL AND ric <> '' "
         "AND ric = upper(COALESCE(underlying, ticker_corto))")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="borra en serio (default: dry-run)")
    a = ap.parse_args()

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT ticker_corto, ric FROM mercado.cedears WHERE {WHERE} "
                    "ORDER BY ticker_corto")
        rows = cur.fetchall()
        print(f"{len(rows)} RICs auto-cargados (ric == ticker):")
        for corto, ric in rows:
            print(f"  {corto:<8} → {ric}")

        cur.execute("SELECT ticker_corto, ric FROM mercado.cedears "
                    f"WHERE ric IS NOT NULL AND ric <> '' AND NOT ({WHERE}) "
                    "ORDER BY ticker_corto")
        quedan = cur.fetchall()
        print(f"\n{len(quedan)} RICs que QUEDAN (formato real / carga manual):")
        for corto, ric in quedan:
            print(f"  {corto:<8} → {ric}")

        if not a.apply:
            print("\nDRY-RUN — no se borró nada. Para borrar: --apply")
            return
        cur.execute(f"UPDATE mercado.cedears SET ric = NULL WHERE {WHERE}")
        print(f"\n✓ {cur.rowcount} filas limpiadas.")


if __name__ == "__main__":
    main()
