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

DECISIÓN DEL USER (2026-08-13): SE BAJA
=======================================
Las 2.106 filas entraron en 44 segundos el 2026-08-06 y no se tocaron nunca
más (`n_tup_ins` == total de filas). Nadie la lee, nadie la escribe, no está en
el schema: es un resto de experimento, no una funcionalidad a medias. Y el dato
**es recuperable**: ese mismo backfill lo sacó de Aunesa, así que si algún día
se retoma [T2] se vuelve a traer.

SEGURIDAD
=========
Por defecto es DRY-RUN: informa y no toca nada. Con `--confirmar` primero
**respalda la tabla a un CSV** y recién ahí la borra; si el respaldo falla, NO
borra. La red cuesta un segundo y hace reversible algo que no lo es.

Uso (en el Droplet):
    python -m scripts.fix_borrar_tabla_muerta              # informe / dry-run
    python -m scripts.fix_borrar_tabla_muerta --confirmar  # respalda y borra
"""
from __future__ import annotations

import os
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


def _respaldar(cur, filas: int) -> str | None:
    """Vuelca la tabla a un CSV al lado del repo. Devuelve la ruta, o None si
    falló (y entonces NO se borra: sin red no se tira nada).

    Es una red, no un requisito: el dato es recuperable de Aunesa —de ahí salió
    en el backfill del 2026-08-06—. Pero cuesta un segundo y hace reversible una
    decisión que no lo es."""
    destino = os.path.abspath(f"{_TABLA}_respaldo.csv")
    try:
        with open(destino, "w", encoding="utf-8") as fh, cur.copy(
            f"COPY {_SCHEMA}.{_TABLA} TO STDOUT WITH CSV HEADER"
        ) as copy:
            for bloque in copy:
                fh.write(bytes(bloque).decode("utf-8"))
        tam = os.path.getsize(destino)
        print(f"   ✅ respaldo: {destino}  ({filas} filas, {tam / 1024:.0f} KB)")
        return destino
    except Exception as e:
        print(f"   respaldo FALLÓ: {str(e).splitlines()[0]}")
        return None


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

        if filas and not confirmar:
            print(f"\n⛔ TIENE {filas} FILAS — no la borro (dry-run). Forensia:")
            _forensia(cur)
            print("\n   Para bajarla igual (respalda a CSV y después borra):")
            print("   python -m scripts.fix_borrar_tabla_muerta --confirmar")
            return 1

        if filas:
            # Decisión del user (2026-08-13): la tabla no la lee nadie, no la
            # escribe nadie desde el backfill único del 2026-08-06 y no está en
            # sql/schema.sql — es un resto de experimento. El dato ES
            # recuperable (ese mismo backfill lo sacó de Aunesa en 44s), así que
            # el respaldo es una red, no un requisito.
            print(f"\n   {filas} filas → respaldo ANTES de borrar:")
            _forensia(cur)
            destino = _respaldar(cur, filas)
            if destino is None:
                print("\n   ⛔ El respaldo falló — NO borro. Sin red no se tira nada.")
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
