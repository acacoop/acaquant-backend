"""scripts/diag_sql_perf.py — diagnóstico de PERFORMANCE de la capa SQL (read-only).

Mide la VERDAD de prod para guiar la optimización (REGLA #2): no asumir, medir.
Corre en el Droplet (donde está el `.env` con POSTGRES_URI):

    python -m scripts.diag_sql_perf

Reporta, sin escribir nada ni exponer el secreto:
  1. HOST:PORT de POSTGRES_URI — para saber si va por el POOLER de Supabase
     (pgbouncer, :6543, transaction mode) o conexión directa (:5432). El pooler
     baja MUCHO la latencia cuando hay muchas conexiones cortas.
  2. RTT de red — `SELECT 1` N veces: cada round-trip a Supabase paga latencia de
     red. Si una vista hace 8 queries secuenciales y el RTT es 80ms → +640ms solos.
  3. Warmup del pool — primera conexión (handshake+TLS) vs siguientes (reuso).
  4. Timing END-TO-END de las funciones de servicio calientes (lo que siente el user).
  5. EXPLAIN ANALYZE de las queries representativas — marca 🔴 si hay Seq Scan en
     tabla grande (índice faltante o no usado).

Todo es SELECT/EXPLAIN — no muta nada.
"""
from __future__ import annotations

import time
from urllib.parse import urlparse


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


def _host_port() -> None:
    from core.postgres import get_postgres_uri
    u = urlparse(get_postgres_uri())
    port = u.port or 5432
    host = u.hostname or ""
    user = u.username or ""
    # Supabase SESSION pooler: host *.pooler.supabase.com:5432 y user
    # "postgres.<project>". El viejo check marcaba ⚠ por ver :5432 — falso
    # positivo (el session pooler TAMBIÉN es :5432; el :6543 es transaction
    # mode, que NO aplica a nuestros pools long-lived). Fix 2026-07-18.
    es_pooler = "pooler.supabase.com" in host or user.startswith("postgres.")
    if es_pooler:
        modo = "POOLER Supabase (session mode :5432) ✅"
    elif port == 6543:
        modo = "POOLER pgbouncer (transaction mode) ✅"
    else:
        modo = "CONEXIÓN DIRECTA ⚠️ — evaluá el session pooler de Supabase"
    print("\n① CONEXIÓN")
    print(f"   host: {host} · user: {user[:24]}")
    print(f"   port: {port} → {modo}")


def _rtt(n: int = 10) -> None:
    from core.postgres import get_pool
    pool = get_pool()
    lat = []
    for _ in range(n):
        t0 = time.perf_counter()
        with pool.connection() as conn:
            conn.execute("SELECT 1")
        lat.append(_ms(t0))
    lat.sort()
    print(f"\n② RTT por query (pool caliente, {n} muestras)")
    print(f"   min {lat[0]}ms · mediana {lat[len(lat)//2]}ms · max {lat[-1]}ms")
    if lat[len(lat)//2] > 30:
        print("   🔴 RTT alto: cada query cuesta caro → la latencia es de RED. "
              "Una vista con N queries paga N×RTT. Priorizar: (a) pooler Supabase, "
              "(b) reducir round-trips por request, (c) misma región Droplet↔Supabase.")


def _warmup() -> None:
    import psycopg

    from core.postgres import _SEARCH_PATH, get_postgres_uri
    t0 = time.perf_counter()
    conn = psycopg.connect(get_postgres_uri(), options=f"-c search_path={_SEARCH_PATH}")
    cold = _ms(t0)
    conn.close()
    print(f"\n③ Conexión FRÍA (handshake+TLS a Supabase): {cold}ms")
    if cold > 200:
        print(f"   🔴 {cold}ms por conexión nueva → si el pool abre conexiones bajo "
              "carga (min_size bajo), cada una cuesta esto. Subir min_size / usar pooler.")


def _time_funcs() -> None:
    print("\n④ Timing de funciones de servicio — 3 columnas:")
    print("   FRÍO = 1ª llamada (cold imports + sub-caches vacíos, == arrancar un worker)")
    print("   RECALC = recompute con imports calientes (lo que paga 1 request cada TTL)")
    print("   CACHE = 2º hit dentro del TTL (lo que pagan TODOS los demás requests)")
    from api.cache import invalidate
    rf = __import__("api.services.renta_fija_sql", fromlist=["x"])
    cs = __import__("core.curvas_sql", fromlist=["x"])
    r18 = __import__("api.services.research_1816_sql", fromlist=["x"])
    rbc = __import__("api.services.research_bcra_sql", fromlist=["x"])
    rsq = __import__("api.services.research_sql", fromlist=["x"])
    ek = __import__("core.eikon_live", fromlist=["x"])
    casos = [
        ("get_renta_fija", "get_renta_fija", lambda: rf.get_renta_fija()),
        ("listar_curva(cer)", "listar_curva", lambda: rf.listar_curva(curva="cer")),
        ("listar_curva(tasa_fija)", "listar_curva", lambda: rf.listar_curva(curva="tasa_fija")),
        ("get_historico_curva(cer)", "get_historico_curva", lambda: rf.get_historico_curva(curva="cer")),
        ("curvas_sql.cargar_todos", None, lambda: cs.cargar_todos()),
        # ── vista RESEARCH (agregado 2026-07-18) ──
        ("1816 universo", "universo", lambda: r18.universo()),
        ("1816 spread(AL30,GD30,tea)", None,
         lambda: r18.spread(a="AL30", b="GD30", campo="tea")),
        ("1816 series(AL30+GD30,tea)", None,
         lambda: r18.series(tickers=["AL30", "GD30"], campo="tea")),
        ("bcra bloques", "bloques", lambda: rbc.bloques()),
        ("bcra series(1,5,7 ×1año)", None, lambda: rbc.series(ids=[1, 5, 7])),
        ("reportes listar (mails)", "listar_research",
         lambda: rsq.listar_research(limit=30, offset=0)),
        ("reuters tablero (poll 5s!)", None, lambda: ek.tablero_reuters()),
    ]
    print(f"   {'función':36}{'frío':>9}{'recalc':>9}{'cache':>8}")
    for nombre, cache_fn, fn in casos:
        try:
            t0 = time.perf_counter(); r = fn(); cold = _ms(t0)
            if cache_fn:
                invalidate(cache_fn)
            t1 = time.perf_counter(); fn(); recalc = _ms(t1)
            t2 = time.perf_counter(); fn(); cache = _ms(t2)
            n = len(r) if hasattr(r, "__len__") else "?"
            flag = " 🔴" if recalc > 150 else (" 🟡" if recalc > 60 else " ✅")
            print(f"   {nombre:36}{cold:>8}ms{recalc:>8}ms{cache:>7}ms  ({n}){flag}")
        except Exception as e:
            print(f"   {nombre:36}  ERROR: {str(e).splitlines()[0][:66]}")
    print("   → RECALC es el número clave; CACHE ≈0 confirma que el cache amortigua.")


_EXPLAIN_QUERIES = [
    ("market_snapshot por ticker (get_renta_fija)",
     "SELECT ticker, last_price, tea, tem, duration, paridad FROM mercado.market_snapshot "
     "WHERE last_price > 0"),
    ("curvas por curva (listar_curva)",
     "SELECT data FROM mercado.curvas WHERE curva = 'cer'"),
    ("snapshots_cierre_hist por curva (historico)",
     "SELECT fecha, ticker, tea FROM mercado.snapshots_cierre_hist WHERE curva = 'cer' "
     "ORDER BY fecha, ticker"),
    ("tenencia último snapshot (AuM)",
     "SELECT id_cuenta, SUM(valuacion) FROM portafolio.tenencia "
     "WHERE fecha = (SELECT max(fecha) FROM portafolio.tenencia WHERE aum='si') AND aum='si' "
     "GROUP BY id_cuenta"),
    ("negocio_movimientos por cuenta (PnL/comercial)",
     "SELECT id_cuenta, SUM(abs(COALESCE(importe,0))) FROM operaciones.negocio_movimientos "
     "WHERE fecha >= '2026-01-01' GROUP BY id_cuenta"),
    # ── vista RESEARCH (agregado 2026-07-18) ──
    ("mkt_1816 spread A−B (self-join)",
     "SELECT x.fecha, (x.valor - y.valor) FROM research.mkt_1816_series x "
     "JOIN research.mkt_1816_series y ON y.fecha = x.fecha AND y.ticker = 'GD30' "
     "AND y.campo = 'tea' AND y.fuente='byma' AND y.moneda='ars' AND y.plazo=1 "
     "WHERE x.ticker = 'AL30' AND x.campo = 'tea' AND x.fuente='byma' "
     "AND x.moneda='ars' AND x.plazo=1 AND x.fecha >= now()::date - 182"),
    ("bcra_series batch por bloque",
     "SELECT id_variable, fecha, valor FROM research.bcra_series "
     "WHERE id_variable = ANY(ARRAY[1,5,7]) AND fecha >= now()::date - 365 "
     "ORDER BY id_variable, fecha"),
    ("ia.research timeline (reportes)",
     "SELECT id, fecha, asunto FROM ia.research ORDER BY fecha DESC, id DESC LIMIT 30"),
]


def _explain() -> None:
    from psycopg.rows import dict_row

    from core.postgres import get_pool
    print("\n⑤ EXPLAIN ANALYZE de queries representativas")
    with get_pool().connection() as conn:
        for nombre, sql in _EXPLAIN_QUERIES:
            try:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + sql)
                    plan = "\n".join(r["QUERY PLAN"] for r in cur.fetchall())
                # tiempo de ejecución reportado por el planner
                exec_ms = "?"
                for line in plan.splitlines():
                    if "Execution Time" in line:
                        exec_ms = line.split(":")[1].strip()
                seq = "🔴 Seq Scan" if "Seq Scan" in plan else "✅ index"
                print(f"\n   ▸ {nombre}")
                print(f"     exec: {exec_ms} · {seq}")
                # primera línea del plan (el nodo top) para contexto
                top = next((l.strip() for l in plan.splitlines() if l.strip()), "")
                print(f"     plan: {top[:110]}")
                if "Seq Scan" in plan:
                    for l in plan.splitlines():
                        if "Seq Scan" in l:
                            print(f"            {l.strip()[:110]}")
            except Exception as e:
                print(f"   ▸ {nombre}: ERROR {str(e).splitlines()[0][:80]}")


def main() -> int:
    print("=" * 72)
    print("  DIAGNÓSTICO DE PERFORMANCE SQL — TradingAV (read-only)")
    print("=" * 72)
    _host_port()
    _warmup()
    _rtt()
    _time_funcs()
    _explain()
    print("\n" + "=" * 72)
    print("  Mandale esta salida a Claude para priorizar las optimizaciones.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
