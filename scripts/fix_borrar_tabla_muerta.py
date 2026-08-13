"""scripts/fix_borrar_tabla_muerta.py — qué es `operaciones.tesoreria_movimientos`.

LA HISTORIA (corregida 2026-08-13, con la tabla en la mano)
===========================================================
Arrancó como "tabla fantasma para borrar": no aparece ni UNA vez en el código
(`grep -rn tesoreria_movimientos` → 0), no está en `sql/schema.sql`, y sus 3
índices salieron en el bloque "índices que NUNCA se usaron" de
`scripts.diag_costo_real`. De ahí a "está muerta" hubo un salto: **código
muerto no implica datos viejos**. Corrida en prod: **2.106 filas**.

Lo que sí sabemos, verificado:
  · `git log -S tesoreria_movimientos --all` → el nombre NUNCA estuvo en
    código, en toda la historia del repo. Solo en un DOC.
  · Ese doc es `docs/OPORTUNIDADES.md`, propuesta **[T2] Persistir los
    movimientos bancarios de Aunesa** — que hoy viven 15 segundos y después se
    destruyen solos. La tabla propuesta se llama, textual, así.

O sea: **no es un resto del pasado, es el germen de una propuesta a futuro**
que alguien empezó a materializar fuera del repo. Por eso este script ya no
borra a ciegas: cuando encuentra filas, hace la forensia y la decisión vuelve
al user con datos (¿de cuándo son? ¿alguien sigue escribiendo?).

SEGURIDAD
=========
Por defecto es DRY-RUN. Si la tabla tiene filas, **informa y NO borra** aunque
se pase `--confirmar`: para bajarla hace falta primero decidir qué se hace con
[T2], y eso no lo decide un script.

Uso (en el Droplet):
    python -m scripts.fix_borrar_tabla_muerta              # informe / dry-run
    python -m scripts.fix_borrar_tabla_muerta --confirmar  # borra SOLO si está vacía
"""
from __future__ import annotations

import sys

from core.postgres import get_pool

_SCHEMA = "operaciones"
_TABLA = "tesoreria_movimientos"


_TIPOS_FECHA = ("date", "timestamp with time zone", "timestamp without time zone")


def _forensia(cur) -> None:
    """De cuándo son los datos y si alguien los sigue escribiendo.

    Las tres preguntas que deciden: qué forma tiene (¿es lo que [T2] proponía?),
    qué período cubre, y si hubo INSERT/UPDATE desde el último reset de stats
    de Postgres — eso último distingue "experimento abandonado" de "algo lo
    alimenta y no sabemos qué".

    A propósito NO imprime filas de muestra: son movimientos bancarios con
    cliente, CUIT e importes, y esta salida se pega en un chat. Los nombres de
    columna y el período alcanzan para identificar qué es."""
    cur.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
        (_SCHEMA, _TABLA))
    cols = cur.fetchall()
    print(f"\n   COLUMNAS ({len(cols)}): "
          + ", ".join(f"{c[0]}" for c in cols[:18])
          + (" …" if len(cols) > 18 else ""))

    fechas = [c[0] for c in cols if c[1] in _TIPOS_FECHA]
    if fechas:
        print("\n   PERÍODO QUE CUBRE:")
        for col in fechas[:3]:
            # Identificadores salidos de information_schema de una tabla FIJA
            # (no hay input de usuario en el camino).
            cur.execute(f'SELECT min("{col}"), max("{col}") FROM {_SCHEMA}.{_TABLA}')
            lo, hi = cur.fetchone()
            print(f"      {col:<24} {lo}  →  {hi}")

    cur.execute(
        "SELECT n_tup_ins, n_tup_upd, n_tup_del, last_autovacuum, last_autoanalyze "
        "FROM pg_stat_user_tables WHERE schemaname = %s AND relname = %s",
        (_SCHEMA, _TABLA))
    st = cur.fetchone()
    cur.execute("SELECT stats_reset FROM pg_stat_database WHERE datname = current_database()")
    reset = cur.fetchone()[0]
    if st:
        # stats_reset viene NULL si nunca se reseteó (cluster nuevo): no
        # formatear a ciegas.
        desde = f"{reset:%Y-%m-%d %H:%M} UTC" if reset else "siempre (sin reset)"
        print(f"\n   ESCRITURAS desde el reset de stats ({desde}):")
        print(f"      INSERT {st[0]}   UPDATE {st[1]}   DELETE {st[2]}")
        if not any(st[:3]):
            print("      → CERO. Nadie la escribió en toda esa ventana: los datos")
            print("        son anteriores y la tabla está quieta.")
        else:
            print("      → HAY escrituras. Algo la alimenta y NO es este repo.")


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
            print(f"\n⛔ TIENE {filas} FILAS — no la borro. Forensia:")
            _forensia(cur)
            print("\n   DECISIÓN (es tuya, no de un script):")
            print("   · Si el último dato es viejo y nadie escribe → experimento")
            print("     abandonado: se puede bajar (o dejar como semilla de [T2]).")
            print("   · Si hay escrituras recientes → algo la alimenta desde")
            print("     FUERA del repo y hay que encontrarlo ANTES de tocarla.")
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
