"""diag_negocio_perf.py — radiografía de performance del dominio NEGOCIO (read-only).

El user reporta DELAY en las cargas de las vistas de negocio (MOVIMIENTOS /
NEGOCIO / aranceles / diferencias / acreencias), que son las tablas que más
crecen día a día. REGLA #2: antes de optimizar nada, medir. Este diag corre en
el Droplet y devuelve los números que deciden QUÉ optimizar:

  1. Tamaño y salud de las tablas del dominio (filas vivas/muertas, tamaño
     tabla vs índices, seq scans vs index scans, último autovacuum).
  2. Uso real de cada índice (idx_scan) — detecta índices muertos y tablas
     que están escaneando secuencial.
  3. Timing end-to-end de los services que sirven las vistas (1ª llamada =
     fría; la 2ª repetida distingue query lenta vs payload) con parámetros
     típicos (últimos 30 días).

Uso (Droplet):
    python -m scripts.diag_negocio_perf            # tablas + índices + timings
    python -m scripts.diag_negocio_perf --sin-timings   # solo tablas + índices

Read-only: no escribe, no borra, no EXPLAIN ANALYZE de queries pesadas.
"""
from __future__ import annotations

import sys
import time
from datetime import UTC, datetime, timedelta

from core.postgres import get_pool

# Tablas del dominio negocio + las que joinean sus vistas ('schema.tabla').
_TABLAS = [
    "operaciones.operaciones",
    "operaciones.negocio_movimientos",
    "operaciones.movimientos",
    "operaciones.acreencias",
    "clientes.comitentes",
    "portafolio.tenencia",
]


def _tabla_stats() -> None:
    print("═" * 78)
    print("1) TABLAS — tamaño, filas, muertas, scans, autovacuum")
    print("═" * 78)
    sql = """
        SELECT s.schemaname, s.relname,
               pg_total_relation_size(s.relid)          AS bytes_total,
               pg_relation_size(s.relid)                AS bytes_tabla,
               pg_indexes_size(s.relid)                 AS bytes_idx,
               s.n_live_tup, s.n_dead_tup,
               s.seq_scan, s.idx_scan,
               to_char(s.last_autovacuum, 'MM-DD HH24:MI') AS last_av,
               to_char(s.last_autoanalyze, 'MM-DD HH24:MI') AS last_aa
        FROM pg_stat_user_tables s
        WHERE s.schemaname || '.' || s.relname = ANY(%(tablas)s)
        ORDER BY pg_total_relation_size(s.relid) DESC
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"tablas": _TABLAS})
        rows = cur.fetchall()
    hdr = (f"{'tabla':<34}{'total':>9}{'datos':>9}{'índices':>9}"
           f"{'vivas':>10}{'muertas':>9}{'seq':>8}{'idx':>10}  autovac")
    print(hdr)
    for (sch, rel, bt, bd, bi, live, dead, seq, idx, av, _aa) in rows:
        mb = lambda b: f"{(b or 0) / 1048576:.0f}MB"  # noqa: E731
        print(f"{sch + '.' + rel:<34}{mb(bt):>9}{mb(bd):>9}{mb(bi):>9}"
              f"{live or 0:>10}{dead or 0:>9}{seq or 0:>8}{idx or 0:>10}  {av or '—'}")
    print("\nLectura: `seq` alto con tabla grande = queries sin índice adecuado.")
    print("`muertas` alto vs vivas = falta vacuum (bloat) → todo se vuelve más lento.")


def _indices() -> None:
    print()
    print("═" * 78)
    print("2) ÍNDICES — uso real (idx_scan=0 desde el último reset = candidato muerto)")
    print("═" * 78)
    sql = """
        SELECT i.schemaname, i.relname, i.indexrelname,
               pg_relation_size(i.indexrelid) AS bytes, i.idx_scan
        FROM pg_stat_user_indexes i
        WHERE i.schemaname || '.' || i.relname = ANY(%(tablas)s)
        ORDER BY i.relname, i.idx_scan DESC
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"tablas": _TABLAS})
        for (sch, rel, idx, b, scans) in cur.fetchall():
            marca = "  ← SIN USO" if (scans or 0) == 0 else ""
            print(f"{sch + '.' + rel:<30} {idx:<42}{(b or 0) / 1048576:>7.0f}MB"
                  f"{scans or 0:>10}{marca}")


def _timings() -> None:
    print()
    print("═" * 78)
    print("3) TIMINGS de services (1ª = FRÍA; 2ª = repetida: cache de app si el")
    print("   service tiene @cached, o buffers de Postgres calientes si no)")
    print("═" * 78)
    hoy = (datetime.now(UTC) - timedelta(hours=3)).date()
    desde = (hoy - timedelta(days=30)).isoformat()
    hasta = hoy.isoformat()

    # (label, import_path, fn, kwargs típicos de la vista)
    from api.services import operaciones_sql as ops

    candidatos = [
        # ops_serie NO acepta rango: agrega TODA la historia por diseño (el
        # chart de MOVIMIENTOS) — si da alto en frío, ahí hay un candidato.
        ("MOVIMIENTOS /ops/serie (full hist)", ops.ops_serie,
         {"moneda": "ARS"}),
        ("MOVIMIENTOS /ops/resumen 30d",    ops.ops_resumen,
         {"moneda": "ARS", "desde": desde, "hasta": hasta}),
        ("CONSOLIDADO bruto 30d",           ops.ops_consolidado,
         {"metrica": "bruto", "desde": desde, "hasta": hasta}),
        ("ARANCELES /ops/aranceles 30d",    ops.ops_aranceles,
         {"moneda": "ARS", "desde": desde, "hasta": hasta}),
        ("AGRO /ops/agro 30d",              ops.ops_agro,
         {"desde": desde, "hasta": hasta}),
        ("CONTRAPARTES /flujo/resumen 30d", ops.flujo_resumen,
         {"desde": desde, "hasta": hasta}),
    ]
    import inspect
    print(f"{'vista / service':<36}{'fría':>10}{'cache':>10}   nota")
    for label, fn, kwargs in candidatos:
        try:
            firma = inspect.signature(fn)
            kw = {k: v for k, v in kwargs.items() if k in firma.parameters}
            t0 = time.perf_counter()
            fn(**kw)
            fria = time.perf_counter() - t0
            t0 = time.perf_counter()
            fn(**kw)
            cache = time.perf_counter() - t0
            nota = "" if fria < 1.0 else ("← LENTO" if fria < 3.0 else "← MUY LENTO")
            print(f"{label:<36}{fria * 1000:>8.0f}ms{cache * 1000:>8.0f}ms   {nota}")
        except Exception as e:
            print(f"{label:<36}{'—':>10}{'—':>10}   error: {str(e)[:60]}")
    print("\nLectura: si 'fría' es alta pero 'cache' ~0, el problema es la QUERY")
    print("(índice/plan); si ambas son altas, el problema es el volumen de payload")
    print("o serialización. Con esto decidimos dónde operar.")


def main() -> int:
    _tabla_stats()
    _indices()
    if "--sin-timings" not in sys.argv:
        _timings()
    print("\nDiag OK — pegar la salida completa en el chat para decidir el fix.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
