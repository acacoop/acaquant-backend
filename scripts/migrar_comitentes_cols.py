"""scripts/migrar_comitentes_cols.py — agrega a `clientes.comitentes` los campos de
Aunesa que el espejo SQL no tenía, para que Comitentes viva COMPLETO en SQL (la
ingesta `jobs/sync_comitentes` pasa a escribir SQL directo, sin Mongo).

    python -m scripts.migrar_comitentes_cols            # PREVIEW
    python -m scripts.migrar_comitentes_cols --commit   # aplica (idempotente)
"""
from __future__ import annotations

import sys

from core.postgres import get_pool

_COLS = [
    ("tipo_titular", "text"),
    ("tipo", "text"),
    ("clase", "text"),
    ("tipo_cliente", "text"),         # usado por clasificar_nivel_3
    ("perfil_inversion", "text"),
    ("provincia", "text"),
    ("created_at", "timestamptz"),
    ("updated_at", "timestamptz"),
]


def main() -> None:
    commit = "--commit" in sys.argv
    print("\n=== columnas a agregar a clientes.comitentes ===")
    for col, tipo in _COLS:
        print(f"   {col:<20} {tipo}")
    if not commit:
        print("\n[PREVIEW] no se escribió nada. Repetí con --commit.\n")
        return
    with get_pool().connection() as conn, conn.cursor() as cur:
        for col, tipo in _COLS:
            cur.execute(f"ALTER TABLE clientes.comitentes ADD COLUMN IF NOT EXISTS {col} {tipo}")
        conn.commit()
    print("\n[COMMIT] listo.\n")


if __name__ == "__main__":
    main()
