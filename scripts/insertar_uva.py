"""scripts/insertar_uva.py — carga manual del valor UVA en SQL `macro.uva`.

SQL-NATIVE (decomiso Mongo): reemplaza la carga manual en `Trading.UVA` (Mongo).
Lo lee `api/services/macro.get_ultimo_uva` (último por fecha) y la segmentación
patrimonial. UVA cambia ~1×/mes (BCRA) → se corre cuando hay valor nuevo.

    python -m scripts.insertar_uva --valor 1234.56            # fecha = hoy (ART)
    python -m scripts.insertar_uva --valor 1234.56 --fecha 2026-06-28
"""
from __future__ import annotations

import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

from core.postgres import get_pool

ART = ZoneInfo("America/Argentina/Buenos_Aires")


def main() -> int:
    ap = argparse.ArgumentParser(description="Carga manual de UVA en macro.uva.")
    ap.add_argument("--valor", type=float, required=True, help="valor UVA (float).")
    ap.add_argument("--fecha", default=None, help="YYYY-MM-DD (default: hoy ART).")
    args = ap.parse_args()

    fecha = args.fecha or datetime.now(ART).strftime("%Y-%m-%d")
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO uva (fecha, valor) VALUES (%s::date, %s) "
            "ON CONFLICT (fecha) DO UPDATE SET valor = EXCLUDED.valor",
            (fecha, args.valor))
    print(f"UVA cargada: fecha={fecha} valor={args.valor}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
