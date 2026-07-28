"""Diag PERF (read-only): vistas Dólar Futuro y Diferencias Diarias.

Mide en prod lo que el análisis estático solo puede hipotetizar:
  A) Wall-clock de los service calls reales (lo que tarda cada endpoint sin cache),
     con el rango default YTD que usa el frontend.
  B) EXPLAIN (ANALYZE, BUFFERS) de la query representativa de cada vista →
     tipo de scan, filas leídas vs filas devueltas (selectividad).
  C) Volúmenes: filas totales en rango vs filas que sobreviven el filtro.
  D) Índices existentes en ambas tablas (pg_indexes).

Uso (Droplet, fuera de rueda idealmente — es read-only pero pesado):
    python -m scripts.diag_perf_dif_dlr
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from api.services import operaciones_sql as ops
from api.services._sql import _q

HOY = (datetime.now(UTC) - timedelta(hours=3)).strftime("%Y-%m-%d")
YTD = f"{HOY[:4]}-01-01"


def _t(label: str, fn, *a, **kw):
    t0 = time.perf_counter()
    out = fn(*a, **kw)
    ms = (time.perf_counter() - t0) * 1000
    print(f"  {label:<38} {ms:>8.0f} ms")
    return out


def bloque_a():
    print(f"\n=== A) WALL-CLOCK service calls (rango {YTD}..{HOY}) ===")
    # Como los llama el frontend al montar cada vista (sin cache):
    _t("ops_fechas() [monta DLR]", ops.ops_fechas)
    _t("ops_dolar_futuro(YTD)", ops.ops_dolar_futuro, desde=YTD, hasta=HOY)
    _t("ops_diferencias_fechas(USDL)", ops.ops_diferencias_fechas, "USDL")
    _t("ops_diferencias_diarias(YTD,USDL)", ops.ops_diferencias_diarias,
       desde=YTD, hasta=HOY, moneda="USDL")
    # Segundo call igual → mide si el costo es estable (buffers calientes):
    _t("ops_dolar_futuro(YTD) [2da vez]", ops.ops_dolar_futuro, desde=YTD, hasta=HOY)
    _t("ops_diferencias_diarias [2da vez]", ops.ops_diferencias_diarias,
       desde=YTD, hasta=HOY, moneda="USDL")


def _explain(label: str, sql: str, params: dict):
    print(f"\n--- EXPLAIN: {label} ---")
    rows = _q(f"EXPLAIN (ANALYZE, BUFFERS) {sql}", params)
    for r in rows:
        print("  " + r["QUERY PLAN"])


def bloque_b():
    print("\n=== B) EXPLAIN ANALYZE (query representativa de cada vista) ===")
    # DLR por_cuenta — la query tipo de ops_dolar_futuro:
    _explain(
        "DLR por_cuenta (YTD)",
        "SELECT denominacion, SUM(ABS(COALESCE(cantidad,0))*1000) AS noc, count(*) AS n "
        "FROM operaciones WHERE instrumento ILIKE %(dlr)s "
        "AND concertacion >= %(d)s AND concertacion <= %(h)s "
        "GROUP BY denominacion ORDER BY noc DESC NULLS LAST",
        {"dlr": "%DLR%", "d": YTD, "h": HOY},
    )
    # DIF por_cuenta — la query tipo de ops_diferencias_diarias:
    _explain(
        "DIF por_cuenta (YTD, USDL)",
        "SELECT cuenta, SUM(importe) AS imp, COUNT(*) AS n FROM negocio_movimientos "
        "WHERE categoria = 'otro' AND informacion ILIKE %(dif)s AND moneda = %(m)s "
        "AND fecha >= %(d)s AND fecha <= %(h)s "
        "GROUP BY cuenta ORDER BY SUM(ABS(importe)) DESC NULLS LAST",
        {"dif": "Diferencias diarias%", "m": "USDL", "d": YTD, "h": HOY},
    )
    # DIF fechas — el scan sin cota de fecha:
    _explain(
        "DIF fechas (todo el histórico)",
        "SELECT fecha, COUNT(*) AS n FROM negocio_movimientos "
        "WHERE categoria = 'otro' AND informacion ILIKE %(dif)s AND moneda = %(m)s "
        "GROUP BY fecha",
        {"dif": "Diferencias diarias%", "m": "USDL"},
    )


def bloque_c():
    print("\n=== C) SELECTIVIDAD (filas leídas vs útiles, rango YTD) ===")
    r = _q("SELECT count(*) AS total, "
           "count(*) FILTER (WHERE instrumento ILIKE '%%DLR%%') AS dlr "
           "FROM operaciones WHERE concertacion >= %(d)s AND concertacion <= %(h)s",
           {"d": YTD, "h": HOY})[0]
    pct = 100 * r["dlr"] / r["total"] if r["total"] else 0
    print(f"  operaciones YTD: {r['total']:,} filas | DLR: {r['dlr']:,} ({pct:.1f}%)")
    r = _q("SELECT count(*) AS otro, "
           "count(*) FILTER (WHERE informacion ILIKE 'Diferencias diarias%%') AS dif "
           "FROM negocio_movimientos WHERE categoria = 'otro' "
           "AND fecha >= %(d)s AND fecha <= %(h)s", {"d": YTD, "h": HOY})[0]
    pct = 100 * r["dif"] / r["otro"] if r["otro"] else 0
    print(f"  negocio 'otro' YTD: {r['otro']:,} filas | diferencias: {r['dif']:,} ({pct:.1f}%)")


def bloque_d():
    print("\n=== D) ÍNDICES actuales ===")
    for tabla in ("operaciones", "negocio_movimientos"):
        print(f"  [{tabla}]")
        for r in _q("SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = 'operaciones' AND tablename = %(t)s "
                    "ORDER BY indexname", {"t": tabla}):
            print(f"    {r['indexdef']}")


def main():
    print(f"diag_perf_dif_dlr — hoy={HOY}, rango YTD={YTD}..{HOY}")
    bloque_a()
    bloque_b()
    bloque_c()
    bloque_d()
    print("\nListo. Pegar el output completo en el chat.")


if __name__ == "__main__":
    main()
