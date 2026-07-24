"""drop_research_refinitiv.py — borra las 3 tablas Refinitiv del schema research.

La tab ANÁLISIS FUNDAMENTAL de /renta-variable se eliminó al 100% (2026-07-24,
pedido del user: no se usaba). El código (router/service/vista/copiloto/ingesta)
ya salió del repo; esto borra los DATOS. Toca SOLO las 3 tablas Refinitiv —
el resto del schema `research` (mkt_1816_*, bcra_*) es de la vista Research
y NO se toca.

Dry-run por default (muestra qué hay); --apply para dropear. Idempotente
(IF EXISTS: re-correrlo no falla).

Uso:
    python -m scripts.drop_research_refinitiv            # muestra conteos
    python -m scripts.drop_research_refinitiv --apply    # DROP definitivo
"""
from __future__ import annotations

import sys

from core.postgres import get_pool

_TABLAS = ("research.companies", "research.fundamentals", "research.market_snapshot")


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    with get_pool().connection() as conn, conn.cursor() as cur:
        print("== Tablas Refinitiv a borrar ==")
        for t in _TABLAS:
            try:
                cur.execute(f"SELECT count(*) FROM {t}")
                n = cur.fetchone()[0]
                print(f"  {t:<30} {n} filas")
            except Exception:
                conn.rollback()
                print(f"  {t:<30} (no existe — ya borrada)")
        if not apply:
            print("\nDRY-RUN. Con --apply se dropean las 3 (el resto de research.* no se toca).")
            return 0
        for t in _TABLAS:
            cur.execute(f"DROP TABLE IF EXISTS {t}")
        conn.commit()
    print("\n✅ Dropeadas. La tab ya no existe en el código; esto era lo último.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
