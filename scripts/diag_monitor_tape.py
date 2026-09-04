"""`scripts/diag_monitor_tape.py` — ¿AGUANTA EL TAPE la tab MONITOR?

Read-only. No escribe una sola fila.

**Por qué existe.** La tab MONITOR (volumen operado por precio) se piensa sobre
los dos tapes que ya existen, y la única pregunta que decide el diseño no se
puede contestar desde el repo: **cuántos ticks hay por rueda y cuánto tarda
agregarlos**. Sin ese número, elegir la ventana (¿1 rueda? ¿5?) es adivinar —
y adivinar acá se paga con una pantalla que tarda 4 segundos en abrir.

Mide TRES cosas, y las separa a propósito:

  **(1) Qué hay guardado, de verdad.** `mercado.timesales` (renta fija) declara
  retención de 7 días corridos (`prune_native` en `engines/valores.py`, que
  corre al ARRANCAR el motor — o sea 13:20 UTC L-V, por cron). Declarar no es
  tener: si el motor no arrancó un día, hay más; si arrancó dos veces, lo mismo.
  Acá se ve la ventana REAL. Para CEDEARs (`mercado.cedears_time_sales`) el
  esperado es "solo hoy" —lo trunca `jobs/cleanup_cedears_timesales.py`— y si
  aparece más de una fecha, es que ese cron no está corriendo.

  **(2) Cuánto tape tiene un instrumento líquido.** Filas por rueda del ticker,
  con `WHERE ticker = X AND ts >= Y`: exactamente el predicado de la tab, que
  entra por el índice `(ticker, ts DESC)`. NO se escanea la tabla entera.

  **(3) Cuánto tarda lo que la tab va a pedir.** Se cronometran las DOS queries
  reales —la serie por minuto y el perfil por bucket de precio— y se informa
  **cuántas filas devuelven**. Ese contraste es el punto: si 40.000 ticks se
  agregan server-side a ~360 filas en 80 ms, la ventana de 5 ruedas sale gratis;
  si tarda un segundo, la tab arranca en HOY y el multi-rueda espera.

⚠️ **El único agregado que escanearía la tabla entera** (la foto por fecha del
paso 1) se saltea solo si la tabla es grande: primero se lee la estimación de
`pg_class.reltuples` —que es gratis— y recién con ese número se decide. Es la
REGLA #2 aplicada al propio diagnóstico: medir antes de medir. Con `--global` se
fuerza igual.

Corre por el carril de JOBS (`get_job_pool`), aislado del pool que sirve a la
mesa. Aun así conviene correrlo **fuera de rueda** (no 13-20 UTC L-V).

Uso:
    python -m scripts.diag_monitor_tape                    # los defaults
    python -m scripts.diag_monitor_tape --dias 7           # ventana a mirar
    python -m scripts.diag_monitor_tape --rf AL30,GD30     # otros bonos
    python -m scripts.diag_monitor_tape --rv NVDA,AAPL     # otros CEDEARs
    python -m scripts.diag_monitor_tape --global           # + foto por fecha
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from psycopg.rows import dict_row

from core.postgres import get_job_pool

ART = ZoneInfo("America/Argentina/Buenos_Aires")
TZ_ART_SQL = "America/Argentina/Buenos_Aires"

# Umbral del paso 1: por encima de esto, el GROUP BY por fecha escanea demasiado
# y se saltea salvo --global. La estimación sale de pg_class (gratis).
MAX_FILAS_PARA_ESCANEO = 5_000_000

RF_DEFAULT = "AL30,GD30,TX26,S30J6"
RV_DEFAULT = "NVDA,AAPL,TSLA,MELI"


def _q(cur, sql, params=()):
    """Ejecuta y devuelve (filas, milisegundos). El tiempo es lo que se mide."""
    t0 = time.perf_counter()
    cur.execute(sql, params)
    filas = cur.fetchall()
    return filas, (time.perf_counter() - t0) * 1000


def _reltuples(cur, tabla: str) -> float:
    cur.execute(
        "SELECT reltuples::bigint AS n FROM pg_class WHERE oid = %s::regclass", (tabla,))
    fila = cur.fetchone()
    return float(fila["n"]) if fila and fila["n"] is not None else -1.0


# ── (1) qué hay guardado ─────────────────────────────────────────────────────

def cobertura(cur, tabla: str, col_k: str, col_ts: str, tz_aware: bool, forzar: bool) -> None:
    est = _reltuples(cur, tabla)
    print(f"\n── {tabla}")
    print(f"   filas (estimación pg_class): {est:,.0f}".replace(",", "."))

    filas, ms = _q(cur, f"SELECT min({col_ts}) AS lo, max({col_ts}) AS hi FROM {tabla}")
    lo, hi = filas[0]["lo"], filas[0]["hi"]
    if lo is None:
        print("   ⚠️  VACÍA — no hay tape guardado")
        return
    print(f"   ventana real: {lo}  →  {hi}     ({ms:.0f} ms)")
    print(f"   ventana real: {(hi - lo).days} días corridos de punta a punta")

    if est > MAX_FILAS_PARA_ESCANEO and not forzar:
        print(f"   (foto por fecha SALTEADA: la estimación supera "
              f"{MAX_FILAS_PARA_ESCANEO:,} filas → sería un scan completo. "
              f"Forzar con --global)".replace(",", "."))
        return

    expr = f"({col_ts} AT TIME ZONE '{TZ_ART_SQL}')::date" if tz_aware else f"{col_ts}::date"
    filas, ms = _q(cur, f"SELECT {expr} AS fecha, count(*) AS n, "
                        f"count(DISTINCT {col_k}) AS tickers "
                        f"FROM {tabla} GROUP BY 1 ORDER BY 1")
    print(f"   por rueda ({ms:.0f} ms, ESTE es el único que escanea todo):")
    for f in filas:
        print(f"      {f['fecha']}   {f['n']:>10,} filas   {f['tickers']:>4} tickers"
              .replace(",", "."))
    print(f"   → {len(filas)} ruedas guardadas")


# ── (2) y (3) por instrumento ────────────────────────────────────────────────

def _inicio(dias: int, tz_aware: bool):
    """Medianoche ART de hace `dias-1` ruedas corridas. Naive para timesales
    (guarda naive ART, ver sql/schema.sql), aware para el tape de CEDEARs."""
    ahora = datetime.now(ART)
    base = ahora.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=dias - 1)
    return base if tz_aware else base.replace(tzinfo=None)


def medir(cur, *, tabla: str, col_k: str, col_ts: str, tz_aware: bool,
          clave: str, etiqueta: str, dias: int, buckets: int) -> None:
    ini = _inicio(dias, tz_aware)
    fecha_sql = (f"date_trunc('minute', {col_ts} AT TIME ZONE '{TZ_ART_SQL}')"
                 if tz_aware else f"date_trunc('minute', {col_ts})")

    # (2) cuánto tape hay — WHERE por índice (ticker, ts DESC), sin escanear
    dia_sql = (f"({col_ts} AT TIME ZONE '{TZ_ART_SQL}')::date"
               if tz_aware else f"{col_ts}::date")
    filas, ms_cnt = _q(cur, f"SELECT {dia_sql} AS fecha, count(*) AS n "
                            f"FROM {tabla} WHERE {col_k} = %s AND {col_ts} >= %s "
                            f"GROUP BY 1 ORDER BY 1", (clave, ini))
    total = sum(f["n"] for f in filas)
    print(f"\n   {etiqueta}")
    if not total:
        print(f"      sin trades desde {ini}  ({ms_cnt:.0f} ms) — ¿no cotizó, o "
              f"la clave no es la correcta?")
        return
    detalle = "  ".join(f"{f['fecha']}:{f['n']}" for f in filas)
    print(f"      ticks: {total:,} en {len(filas)} rueda(s)   ({ms_cnt:.0f} ms)"
          .replace(",", "."))
    print(f"      por rueda: {detalle}")

    # (3a) la serie por minuto — lo que dibuja el chart de arriba
    serie, ms_serie = _q(cur, f"""
        SELECT {fecha_sql}                                   AS m,
               (array_agg(price ORDER BY {col_ts}, id))[1]    AS o,
               max(price)                                     AS h,
               min(price)                                     AS l,
               (array_agg(price ORDER BY {col_ts} DESC, id DESC))[1] AS c,
               COALESCE(sum(size), 0)                         AS vol,
               count(*)                                       AS trades
        FROM {tabla}
        WHERE {col_k} = %s AND {col_ts} >= %s
        GROUP BY 1 ORDER BY 1
    """, (clave, ini))

    # (3b) el perfil por bucket de precio — lo que dibuja el chart de abajo.
    # El tope se EMPUJA un poco por dos razones distintas: si `hi = lo` (un solo
    # precio en toda la ventana) width_bucket revienta, y si no se empuja, el
    # precio máximo cae en el bucket n+1 — un bucket fantasma de un solo tick,
    # que en un papel ilíquido puede salir como POC.
    perfil, ms_perfil = _q(cur, f"""
        WITH r AS (
            SELECT min(price) AS lo, max(price) AS hi
            FROM {tabla}
            WHERE {col_k} = %s AND {col_ts} >= %s AND price IS NOT NULL
        )
        SELECT width_bucket(t.price, r.lo,
                            CASE WHEN r.hi > r.lo THEN r.hi + (r.hi - r.lo) / 1000
                                 ELSE r.lo + 1 END,
                            %s)                    AS bucket,
               min(t.price)                        AS px_lo,
               max(t.price)                        AS px_hi,
               COALESCE(sum(t.size), 0)            AS vol,
               count(*)                            AS trades
        FROM {tabla} t, r
        WHERE t.{col_k} = %s AND t.{col_ts} >= %s AND t.price IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """, (clave, ini, buckets, clave, ini))

    print(f"      serie x minuto: {len(serie):>5} filas devueltas   {ms_serie:>7.0f} ms")
    print(f"      perfil x precio:{len(perfil):>5} filas devueltas   {ms_perfil:>7.0f} ms")
    if total:
        print(f"      compresión: {total:,} ticks → {len(serie) + len(perfil)} filas por el cable"
              .replace(",", "."))
    if perfil:
        poc = max(perfil, key=lambda f: f["vol"] or 0)
        print(f"      POC de la ventana: {poc['px_lo']}–{poc['px_hi']}  "
              f"vol {poc['vol']}  ({poc['trades']} trades)")


def resolver_rf(cur, cortos: list[str]) -> list[tuple[str, str]]:
    """ticker corto → símbolo de mercado. En `mercado.curvas` la PK `ticker` es
    el corto (AL30) e `instrumento` es el símbolo que se le manda a Primary —
    que es lo que `engines/valores.py` guarda en `mercado.timesales.ticker`."""
    if not cortos:
        return []
    cur.execute(
        "SELECT ticker AS corto, instrumento AS simbolo FROM mercado.curvas "
        "WHERE ticker = ANY(%s)", (cortos,))
    hallados = {r["corto"]: r["simbolo"] for r in cur.fetchall() if r["simbolo"]}
    for c in cortos:
        if c not in hallados:
            print(f"   ⚠️  {c}: no está en mercado.curvas (o no tiene símbolo) — se saltea")
    return [(c, hallados[c]) for c in cortos if c in hallados]


def main() -> int:
    ap = argparse.ArgumentParser(description="¿aguanta el tape la tab MONITOR? (read-only)")
    ap.add_argument("--dias", type=int, default=7, help="ventana a medir (default 7 corridos)")
    ap.add_argument("--buckets", type=int, default=26, help="buckets de precio del perfil")
    ap.add_argument("--rf", default=RF_DEFAULT, help="CSV de tickers cortos de renta fija")
    ap.add_argument("--rv", default=RV_DEFAULT, help="CSV de ticker_corto de CEDEARs")
    ap.add_argument("--global", dest="forzar", action="store_true",
                    help="forzar la foto por fecha aunque la tabla sea grande")
    args = ap.parse_args()

    rf = [t.strip().upper() for t in args.rf.split(",") if t.strip()]
    rv = [t.strip().upper() for t in args.rv.split(",") if t.strip()]

    print("=" * 78)
    print(f"TAPE PARA LA TAB MONITOR — ventana {args.dias} días corridos, "
          f"{args.buckets} buckets de precio")
    print(f"ahora: {datetime.now(ART):%Y-%m-%d %H:%M:%S} ART")
    print("=" * 78)

    with get_job_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        print("\n" + "─" * 78)
        print("(1) QUÉ HAY GUARDADO")
        print("─" * 78)
        cobertura(cur, "mercado.timesales", "ticker", "ts",
                  tz_aware=False, forzar=args.forzar)
        cobertura(cur, "mercado.cedears_time_sales", "ticker_corto", "ts",
                  tz_aware=True, forzar=args.forzar)

        print("\n" + "─" * 78)
        print("(2)+(3) POR INSTRUMENTO — el predicado y las queries REALES de la tab")
        print("─" * 78)

        print("\n RENTA FIJA  ·  mercado.timesales  ·  clave = símbolo de mercado")
        for corto, simbolo in resolver_rf(cur, rf):
            medir(cur, tabla="mercado.timesales", col_k="ticker", col_ts="ts",
                  tz_aware=False, clave=simbolo, etiqueta=f"{corto}  ({simbolo})",
                  dias=args.dias, buckets=args.buckets)

        print("\n RENTA VARIABLE  ·  mercado.cedears_time_sales  ·  clave = ticker_corto")
        for corto in rv:
            medir(cur, tabla="mercado.cedears_time_sales", col_k="ticker_corto", col_ts="ts",
                  tz_aware=True, clave=corto, etiqueta=corto,
                  dias=args.dias, buckets=args.buckets)

    print("\n" + "=" * 78)
    print("CÓMO SE LEE:")
    print("  · 'ventana real' del paso 1 = lo que HAY, no lo que la retención declara.")
    print("  · 'ticks' del paso 2 = el volumen crudo que la tab NO baja (agrega en el server).")
    print("  · los ms del paso 3 son el costo de abrir la tab con esa ventana.")
    print("    Referencia: por debajo de ~150 ms la ventana entra sin que se note.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
