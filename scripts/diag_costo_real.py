"""scripts/diag_costo_real.py — DÓNDE se va el tiempo, con números (read-only).

Existe para no optimizar por corazonada (REGLA #2). Cruza las dos miradas que
hacen falta para decidir si un cambio va a ser una mejora REAL:

    APP  — qué endpoints consumen más tiempo total (n × avg, no el más lento:
           un endpoint de 3s que se llama 2 veces por día importa menos que uno
           de 200ms que se pollea cada 10s). Sale de manager.latencia_endpoints,
           que ya alimenta Manager → OBSERVABILIDAD: acá se reusa el MISMO
           service, así las dos vistas no pueden contradecirse.
    BASE — qué queries y qué tablas cuestan de verdad adentro de Postgres.

Y sirve para clasificar el problema, que es lo que decide el remedio:

    · La base casi no aparece  → el tiempo está en Python o en una API externa
                                 (ej. Aunesa: 1.6s de los 2.0s de Tesorería).
                                 Optimizar SQL ahí no mueve la aguja.
    · Muchas queries baratas   → se paga PEAJE DE RED (~8.5ms cada una, medido
                                 2026-08-13). Se arregla agrupando/cacheando,
                                 NO indexando.
    · Una query cara           → ahí sí: índice, o repensar la forma de la tabla.
    · Tabla GRANDE escaneada   → candidata a índice o a agregado (el patrón que
                                 ya funcionó en operaciones.ops_agregado_diario).

OJO: los contadores de Postgres son ACUMULADOS desde el último reset de stats
(se imprime la fecha). Comparan bien entre sí, no son "de hoy".

Uso (en el Droplet):
    python -m scripts.diag_costo_real [horas] [top]   # default 24h, top 12
    python -m scripts.diag_costo_real 168 60          # una semana, 60 endpoints
"""
from __future__ import annotations

import sys

from core.postgres import get_pool

_TOP = 12
# Una tabla chica escaneada entera es ÓPTIMO (no hay plan más barato que leer
# 30 filas), así que solo se marca ⚠ si además es grande. Sin este piso, el
# diag mandaría a indexar catálogos y empeoraría las escrituras a cambio de nada.
_FILAS_GRANDE = 10_000


def _q(cur, sql: str, params: tuple = ()) -> list[dict]:
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _mb(b) -> str:
    return f"{(b or 0) / 1024 ** 2:8.1f} MB"


def _corta(q: str, n: int = 88) -> str:
    """La query a UNA línea: pg_stat_statements las guarda con saltos."""
    limpia = " ".join((q or "").split())
    return limpia[:n] + ("…" if len(limpia) > n else "")


def _app(horas: int, top: int) -> None:
    print(f"\n{'=' * 78}\n1) APP — endpoints por TIEMPO TOTAL consumido "
          f"(últimas {horas}h)\n{'=' * 78}")
    try:
        from api.services import latencia_endpoints as svc
        # Se piden MUCHOS y se recorta acá: así se puede decir qué fracción del
        # tiempo total cubre el top mostrado. Con un top chico, una familia
        # entera (ej. todo /api/manager/*) queda invisible y parece que "no
        # pasa nada ahí" — que es justamente la trampa.
        data = svc.get_latencia(horas=horas, top=500)
    except Exception as e:
        print(f"   no disponible ({e})")
        return
    eps = data.get("endpoints") or []
    if not eps:
        print("   sin telemetría en la ventana (¿API recién reiniciada?)")
        return
    total_todos = sum((e["n"] * e["avg_ms"]) / 1000 for e in eps)
    print(f"   {'endpoint':<44}{'n':>7}{'avg':>8}{'max':>8}{'total':>10}")
    acum = 0.0
    for e in eps[:top]:
        total_s = (e["n"] * e["avg_ms"]) / 1000
        acum += total_s
        print(f"   {e['endpoint'][:43]:<44}{e['n']:>7}{e['avg_ms']:>7.0f}ms"
              f"{e['max_ms']:>7.0f}ms{total_s:>9.1f}s")
    pct = (acum / total_todos * 100) if total_todos else 0
    print(f"   → mostrados {min(top, len(eps))} de {len(eps)} endpoints: cubren "
          f"{pct:.0f}% de los {total_todos:.0f}s totales de la ventana.")
    print("   → el que más SUMA es el que conviene mirar, no el más lento suelto.")


def _queries(cur) -> None:
    print(f"\n{'=' * 78}\n2) BASE — queries por tiempo total dentro de Postgres"
          f"\n{'=' * 78}")
    try:
        # El patrón va por PARÁMETRO: con params, un `%` literal en el SQL
        # revienta el binder de psycopg (lo lee como placeholder).
        filas = _q(cur, """
            SELECT calls, total_exec_time, mean_exec_time, rows, query
            FROM pg_stat_statements
            WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
              AND query NOT LIKE %s
              AND query <> ALL(%s)
            ORDER BY total_exec_time DESC LIMIT %s
        """, ("%pg_stat%", ["BEGIN", "COMMIT", "ROLLBACK"], _TOP))
    except Exception as e:
        print(f"   pg_stat_statements no disponible ({str(e).splitlines()[0]})")
        print("   (en Supabase se habilita desde Database → Extensions)")
        return
    print(f"   {'calls':>9}{'media':>9}{'total':>10}  query")
    for f in filas:
        print(f"   {f['calls']:>9}{f['mean_exec_time']:>8.1f}ms"
              f"{f['total_exec_time'] / 1000:>9.1f}s  {_corta(f['query'])}")
    print("   → media ALTA = query cara (índice / forma de la tabla).")
    print("     media BAJA con calls ENORME = no es la query, es la CANTIDAD:")
    print("     cada llamada paga ~8.5ms de red aunque ejecute en 0.1ms.")


def _tablas(cur) -> None:
    print(f"\n{'=' * 78}\n3) TABLAS — ¿alguna GRANDE se está leyendo entera?"
          f"\n{'=' * 78}")
    filas = _q(cur, """
        SELECT schemaname, relname, n_live_tup, seq_scan, seq_tup_read, idx_scan,
               pg_total_relation_size(relid) AS bytes
        FROM pg_stat_user_tables
        ORDER BY seq_tup_read DESC NULLS LAST LIMIT %s
    """, (_TOP,))
    print(f"   {'tabla':<40}{'filas':>10}{'seq':>8}{'idx':>9}{'tamaño':>12}")
    for f in filas:
        # ⚠ solo si es grande Y el scan secuencial le gana al índice.
        grande = (f["n_live_tup"] or 0) > _FILAS_GRANDE
        sin_indice = (f["seq_scan"] or 0) > (f["idx_scan"] or 0)
        marca = " ⚠" if (grande and sin_indice) else ""
        nombre = f"{f['schemaname']}.{f['relname']}"[:39]
        print(f"   {nombre:<40}{f['n_live_tup'] or 0:>10}{f['seq_scan'] or 0:>8}"
              f"{f['idx_scan'] or 0:>9}{_mb(f['bytes'])}{marca}")
    print("   → ⚠ = tabla con más de 10k filas donde el scan secuencial le gana")
    print("     al índice: candidata a índice o a agregado precomputado.")
    print("     SIN ⚠ no se toca: escanear una tabla chica ES el plan óptimo.")


def _indices(cur) -> None:
    print(f"\n{'=' * 78}\n4) ÍNDICES que NUNCA se usaron (peso muerto)"
          f"\n{'=' * 78}")
    filas = _q(cur, """
        SELECT s.schemaname, s.relname, s.indexrelname,
               pg_relation_size(s.indexrelid) AS bytes,
               i.indisunique OR i.indisprimary AS restriccion
        FROM pg_stat_user_indexes s
        JOIN pg_index i ON i.indexrelid = s.indexrelid
        WHERE s.idx_scan = 0
        ORDER BY pg_relation_size(s.indexrelid) DESC LIMIT %s
    """, (_TOP,))
    if not filas:
        print("   ninguno — todos los índices se están usando.")
        return
    for f in filas:
        # Un índice único/PK sin lecturas NO es basura: sostiene una restricción
        # de integridad. Borrarlo permitiría duplicados. Se marca para no errarle.
        nota = "  ← PK/único: NO borrar (sostiene una restricción)" if f["restriccion"] else ""
        print(f"   {f['schemaname']}.{f['relname']}.{f['indexrelname']}"
              f"  {_mb(f['bytes'])}{nota}")
    print("   → un índice que nadie lee igual se ACTUALIZA en cada INSERT/UPDATE:")
    print("     se paga en escritura y en disco. Los que no sostienen")
    print("     restricción son candidatos a borrar.")


def main() -> int:
    horas = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    top = int(sys.argv[2]) if len(sys.argv) > 2 else _TOP
    _app(horas, top)
    pool = get_pool()
    with pool.connection() as conn, conn.cursor() as cur:
        # autocommit: si una consulta falla (ej. pg_stat_statements sin instalar)
        # NO deja la transacción abortada y los bloques siguientes igual corren.
        # Verificado en un Postgres local sin la extensión: sin esto, el diag
        # moría con "current transaction is aborted" y perdías tablas e índices.
        conn.autocommit = True
        reset = _q(cur, "SELECT stats_reset FROM pg_stat_database "
                        "WHERE datname = current_database()")
        if reset and reset[0]["stats_reset"]:
            print(f"\n(contadores de Postgres acumulados desde "
                  f"{reset[0]['stats_reset']:%Y-%m-%d %H:%M} UTC)")
        _queries(cur)
        _tablas(cur)
        _indices(cur)
    print("""
CÓMO DECIDIR CON ESTO
  1. Mirá el bloque 1: ¿qué endpoint suma más tiempo? Ese es el único que
     conviene tocar primero.
  2. Buscá sus queries en el bloque 2. Si NO aparecen, el tiempo no está en la
     base: no hay nada que optimizar en SQL.
  3. Si aparecen con media baja y muchas calls → agrupar/cachear (peaje de red).
     Si aparecen con media alta → bloque 3 y 4: índice o forma de la tabla.
  4. Lo que no salga en ninguna lista, NO se toca: sería trabajo sin mejora.""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
