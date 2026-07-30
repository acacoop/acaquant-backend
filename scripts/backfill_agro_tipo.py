"""Backfill one-shot: clasifica AGRO en operaciones.operaciones (tipo_agro + opciones).

Contexto: hasta ahora `clasificar_commodity` solo etiquetaba FUTUROS agro; las
OPCIONES agropecuarias quedaban con `commodity` NULL (invisibles en /ops/agro).
Este backfill deja el histórico consistente con el nuevo clasificador:

  A) OPCIONES agro ('Opciones Agropecuarios - Compra/Venta', instrumento
     [SOJ/TRI/MAI.ROS/...]) → setea commodity + tipo_agro = 'OPCION'.
  B) FUTUROS agro ya clasificados (commodity in SOJA/TRIGO/MAIZ, tipo_agro NULL)
     → setea tipo_agro = 'FUTURO'.

Seguro (REGLA #4): scopeado por índice (commodity parcial / tipo_operacion),
batcheado por PK con throttle, idempotente (las guardas `IS NULL` hacen que
re-correrlo no toque nada una vez migrado). Correr fuera de rueda si se puede.

Uso (Droplet): python -m scripts.backfill_agro_tipo
"""
from __future__ import annotations

import time

from core.postgres import get_pool

BATCH = 5000
PAUSA_S = 0.2

_SQL_OPCIONES = """
UPDATE operaciones.operaciones SET
  commodity = CASE
    WHEN instrumento ILIKE '%%SOJ%%' THEN 'SOJA'
    WHEN instrumento ILIKE '%%TRI%%' THEN 'TRIGO'
    WHEN instrumento ILIKE '%%MAI%%' THEN 'MAIZ' END,
  tipo_agro = 'OPCION'
WHERE id IN (
  SELECT id FROM operaciones.operaciones
  WHERE tipo_operacion ILIKE '%%OPCIONES AGROPECUARIO%%'
    AND (commodity IS NULL OR commodity = '')
    AND (instrumento ILIKE '%%SOJ%%' OR instrumento ILIKE '%%TRI%%' OR instrumento ILIKE '%%MAI%%')
  LIMIT %(batch)s)
"""

_SQL_FUTUROS = """
UPDATE operaciones.operaciones SET tipo_agro = 'FUTURO'
WHERE id IN (
  SELECT id FROM operaciones.operaciones
  WHERE commodity IN ('SOJA', 'TRIGO', 'MAIZ') AND tipo_agro IS NULL
    AND tipo_operacion ILIKE '%%FUTUROS%%'
  LIMIT %(batch)s)
"""


def _backfill(cur, conn, titulo: str, sql: str) -> int:
    total = 0
    while True:
        cur.execute(sql, {"batch": BATCH})
        n = cur.rowcount
        conn.commit()
        total += n
        print(f"  [{titulo}] +{n} (acum {total})")
        if n == 0:
            break
        time.sleep(PAUSA_S)
    return total


def main() -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        print("A) Opciones agro → commodity + tipo_agro='OPCION'")
        a = _backfill(cur, conn, "opciones", _SQL_OPCIONES)
        print("B) Futuros agro → tipo_agro='FUTURO'")
        b = _backfill(cur, conn, "futuros", _SQL_FUTUROS)

        cur.execute(
            "SELECT commodity, COALESCE(tipo_agro, '(null)') AS t, count(*) "
            "FROM operaciones.operaciones "
            "WHERE commodity IN ('SOJA', 'TRIGO', 'MAIZ') "
            "GROUP BY commodity, tipo_agro ORDER BY commodity, t"
        )
        print(f"\nOK. Opciones clasificadas: {a} · Futuros marcados: {b}")
        print("\ncommodity · tipo_agro · boletos:")
        for c, t, n in cur.fetchall():
            print(f"  {c:<6} {t:<8} {n}")


if __name__ == "__main__":
    main()
