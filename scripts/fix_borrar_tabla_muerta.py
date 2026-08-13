"""scripts/fix_borrar_tabla_muerta.py — baja `operaciones.tesoreria_movimientos`.

QUÉ ES ESA TABLA
================
Nada. Quedó de una iteración vieja de Tesorería, de antes de decidir que los
movimientos se sirven EN VIVO desde Aunesa y no se persisten. Evidencia
(verificada 2026-08-13):

  · No aparece ni UNA vez en el código (`grep -rn tesoreria_movimientos` → 0).
  · No está en `sql/schema.sql` — o sea que ni siquiera se recrearía.
  · Sus dos índices salieron en el bloque "índices que NUNCA se usaron" de
    `scripts.diag_costo_real`: obvio, nadie la lee.

Es la única cosa de esa lista que conviene borrar: el resto de los índices sin
uso sostienen vistas de uso esporádico (research/BCRA/FRED) y bajarlos sería
riesgo sin premio.

SEGURIDAD
=========
Por defecto es DRY-RUN: informa y no toca nada. Se niega a borrar si la tabla
tiene filas — si aparecieran datos, la premisa "está muerta" es falsa y la
decisión vuelve al user. Borrar exige `--confirmar` explícito.

Uso (en el Droplet):
    python -m scripts.fix_borrar_tabla_muerta              # dry-run
    python -m scripts.fix_borrar_tabla_muerta --confirmar  # la borra
"""
from __future__ import annotations

import sys

from core.postgres import get_pool

_SCHEMA = "operaciones"
_TABLA = "tesoreria_movimientos"


def main() -> int:
    confirmar = "--confirmar" in sys.argv
    pool = get_pool()
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT to_regclass(%s) IS NOT NULL AS existe", (f"{_SCHEMA}.{_TABLA}",))
        if not cur.fetchone()[0]:
            print(f"{_SCHEMA}.{_TABLA} no existe — nada que hacer "
                  "(¿ya se borró en otra corrida?)")
            return 0

        cur.execute(f"SELECT count(*) FROM {_SCHEMA}.{_TABLA}")  # identificador FIJO del módulo, no viene de input
        filas = cur.fetchone()[0]
        cur.execute("SELECT pg_size_pretty(pg_total_relation_size(%s))",
                    (f"{_SCHEMA}.{_TABLA}",))
        tamano = cur.fetchone()[0]
        cur.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = %s AND tablename = %s",
            (_SCHEMA, _TABLA))
        indices = [r[0] for r in cur.fetchall()]

        print(f"{_SCHEMA}.{_TABLA}")
        print(f"   filas   : {filas}")
        print(f"   tamaño  : {tamano}")
        print(f"   índices : {', '.join(indices) or '—'}")

        if filas:
            print(f"\n⛔ TIENE {filas} FILAS — no la borro.")
            print("   La premisa era que estaba muerta; si tiene datos, hay que")
            print("   mirar de dónde salieron antes de tocar nada.")
            return 1

        if not confirmar:
            print("\n(dry-run) Está vacía y nadie la referencia → se puede borrar.")
            print("Para hacerlo:  python -m scripts.fix_borrar_tabla_muerta --confirmar")
            return 0

        cur.execute(f"DROP TABLE {_SCHEMA}.{_TABLA}")  # identificador FIJO del módulo, no viene de input
        conn.commit()
        print(f"\n✅ Borrada. Se fueron también sus {len(indices)} índices.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
