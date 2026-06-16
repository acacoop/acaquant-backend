"""scripts/migrar_arancel_negocio.py — agrega a `operaciones.negocio_movimientos` las
columnas de arancel que vivían SOLO en Mongo, para terminar de migrar el cobro del
proyecto a SQL (jobs/aranceles escribe SQL directo; el auditor /aunesa/boletos/faltantes
lee SQL).

    python -m scripts.migrar_arancel_negocio            # PREVIEW
    python -m scripts.migrar_arancel_negocio --commit   # aplica (idempotente)
"""
from __future__ import annotations

import sys

from core.postgres import get_pool

_COLS = [
    ("arancel", "numeric"),    # atajo ARS — lo lee el auditor de boletos sin arancel
    ("aranceles", "jsonb"),    # desglose por moneda {ARS: x, USD: y}
]


def main() -> None:
    commit = "--commit" in sys.argv
    print("\n=== columnas a agregar a operaciones.negocio_movimientos ===")
    for col, tipo in _COLS:
        print(f"   {col:<12} {tipo}")
    if not commit:
        print("\n[PREVIEW] no se escribió nada. Repetí con --commit.\n")
        return
    with get_pool().connection() as conn, conn.cursor() as cur:
        for col, tipo in _COLS:
            cur.execute(
                f"ALTER TABLE operaciones.negocio_movimientos "
                f"ADD COLUMN IF NOT EXISTS {col} {tipo}")
        conn.commit()
    print("\n[COMMIT] listo.\n")


if __name__ == "__main__":
    sys.exit(main())
