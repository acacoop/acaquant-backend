"""Backfill one-shot: NIVEL 5 vacío → 'NO' en clientes.comitentes.

Rellena con el valor literal 'NO' TODAS las cuentas que hoy tienen el campo
`nivel_5` vacío (NULL o string en blanco). 'NO' pasa a ser un valor más de
NIVEL 5 (aparece en el filtro de AGRO y en el árbol de segmentación del Manager).

Decisiones:
- 'NO' en MAYÚSCULAS: nivel_1..5 se normalizan a upper en el editor
  (api/routers/manager/clientes.py::_UPPERCASE_FIELDS) → mantenemos la convención.
- Solo toca las cuentas VACÍAS (NULL / '' / solo-espacios). NO pisa un nivel_5
  ya cargado. Idempotente: re-correrlo no cambia nada una vez rellenadas.
- Estampa auditoría (actualizado_por/at) para dejar rastro del backfill.

Uso (Droplet): python -m scripts.backfill_nivel5_no
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.postgres import get_pool

ORIGEN = "backfill:nivel5_no"
_VACIO = "(nivel_5 IS NULL OR nivel_5 !~ '\\S')"


def main() -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM clientes.comitentes WHERE {_VACIO}")
        pendientes = cur.fetchone()[0]
        print(f"Cuentas con NIVEL 5 vacío: {pendientes}")
        if not pendientes:
            print("Nada que hacer (no hay cuentas con NIVEL 5 vacío).")
            return

        cur.execute(
            f"UPDATE clientes.comitentes "
            f"SET nivel_5 = 'NO', actualizado_por = %s, actualizado_at = %s "
            f"WHERE {_VACIO}",
            (ORIGEN, datetime.now(UTC)),
        )
        actualizadas = cur.rowcount
        conn.commit()
        print(f"OK: {actualizadas} cuentas rellenadas con NIVEL 5 = 'NO'.")

        cur.execute(
            "SELECT nivel_5, count(*) FROM clientes.comitentes "
            "WHERE nivel_5 IS NOT NULL AND nivel_5 <> '' "
            "GROUP BY nivel_5 ORDER BY count(*) DESC"
        )
        print("\nNIVEL 5 actuales (valor · cuentas):")
        for val, n in cur.fetchall():
            print(f"  {val:<20} {n}")


if __name__ == "__main__":
    main()
