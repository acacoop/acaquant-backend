"""scripts/diag_informe_perf.py — diagnóstico READ-ONLY de performance del INFORME comercial.

Mide, con datos reales de prod, cuánto tarda cada uno de los 4 endpoints de la vista
NEGOCIO → OPERADORES → INFORME y dónde se va el tiempo, SIN escribir nada (solo SELECT +
EXPLAIN ANALYZE). Cumple REGLA #2: no asumimos nada del volumen/plan de las queries — lo
medimos.

Qué imprime:
  1) Wall-clock de cada endpoint (global, peor caso = toda la mesa) y con un filtro de
     ejemplo (scopeado), para ver el costo del path sin/ con filtro madre.
  2) # de filas devueltas por cada uno (tamaño del dataset que viaja al front).
  3) EXPLAIN (ANALYZE, BUFFERS) de los dos scans-base pesados del rollup:
     - agregación sobre `operaciones` (negocio_movimientos NO existe: es `negocio_movimientos`)
     - agregación sobre `negocio_movimientos`
     Ahí se ve si hay Seq Scan, cuántas filas toca y si falta índice.

Uso (en el Droplet, con el venv):
    python -m scripts.diag_informe_perf

REGLA #4: es read-only y liviano, pero EXPLAIN ANALYZE EJECUTA la query → correr FUERA de
rueda (no 13-20 UTC L-V) para no competir con los motores.
"""
from __future__ import annotations

import time
from datetime import date

from api.services import comercial_sql as cs
from api.services._sql import _q

# Un mes corriente como ventana de ejemplo (TOTAL = [desde, hasta]).
_HOY = date.today().isoformat()
_MES_INI = date.today().replace(day=1).isoformat()


def _t(label: str, fn) -> None:
    """Corre `fn`, imprime wall-clock + tamaño del resultado."""
    t0 = time.perf_counter()
    r = fn()
    ms = (time.perf_counter() - t0) * 1000
    n = ""
    if isinstance(r, dict):
        parts = []
        for k, v in r.items():
            if isinstance(v, list):
                parts.append(f"{k}={len(v)}")
        n = " · " + ", ".join(parts) if parts else ""
    print(f"  {label:<52} {ms:>8.0f} ms{n}")


def _explain(titulo: str, sql: str) -> None:
    print(f"\n── EXPLAIN (ANALYZE, BUFFERS): {titulo} ──")
    rows = _q(f"EXPLAIN (ANALYZE, BUFFERS) {sql}")
    for row in rows:
        # EXPLAIN devuelve una sola columna ('QUERY PLAN') por fila.
        print("  " + str(next(iter(row.values()))))


def main() -> None:
    print("=" * 78)
    print(f"DIAG INFORME PERF · hasta={_HOY} · mes_ini={_MES_INI}")
    print("=" * 78)

    print("\n[1] Endpoints GLOBAL (sin filtro madre — peor caso, toda la mesa):")
    _t("informe_comercial", lambda: cs.informe_comercial(moneda="ARS", fecha=_HOY, desde=_MES_INI))
    _t("informe_cuentas_por_segmento", lambda: cs.informe_cuentas_por_segmento(fecha=_HOY))
    _t("informe_aranceles_segmento (op='')", lambda: cs.informe_aranceles_segmento(operador="", moneda="ARS", fecha=_HOY, desde=_MES_INI))
    _t("informe_segmento_detalle ('todos')", lambda: cs.informe_segmento_detalle(segmento="todos", moneda="ARS", fecha=_HOY, desde=_MES_INI))

    print("\n[2] Endpoint informe_comercial con filtro madre de ejemplo (division scopea):")
    # Tomamos una división real de la base para medir el path scopeado.
    div = _q("SELECT division FROM comitentes WHERE division IS NOT NULL "
             "AND estado='Activa' GROUP BY division ORDER BY count(*) DESC LIMIT 1")
    if div:
        d = div[0]["division"]
        print(f"    (division = {d!r})")
        _t("informe_comercial [division]", lambda: cs.informe_comercial(
            moneda="ARS", fecha=_HOY, desde=_MES_INI, division=(d,)))
    else:
        print("    (no hay comitentes con division → salteo)")

    print("\n[3] Planes de los scans-base del rollup (dónde se va el tiempo):")
    # Agregación de VOLUMEN sobre negocio_movimientos (mes en curso, sin scope).
    _explain(
        "negocio_movimientos (volumen del mes)",
        "SELECT id_cuenta, count(*) AS n, sum(abs(coalesce(importe,0))) AS vol "
        "FROM negocio_movimientos "
        f"WHERE unidad IS DISTINCT FROM 'USDL' AND fecha >= '{_MES_INI}' "
        "GROUP BY id_cuenta")
    # Agregación de ARANCEL sobre operaciones (mes en curso, sin scope).
    _explain(
        "operaciones (arancel del mes)",
        "SELECT id_cuenta, sum(arancel) AS ar "
        "FROM operaciones "
        f"WHERE arancel > 0 AND etapa IS DISTINCT FROM 'solicitud' AND concertacion >= '{_MES_INI}' "
        "GROUP BY id_cuenta")

    print("\n[4] Tamaño de las tablas base (para dimensionar los scans):")
    for tbl in ("negocio_movimientos", "operaciones", "comitentes"):
        r = _q(f"SELECT count(*) AS n FROM {tbl}")
        print(f"  {tbl:<28} {r[0]['n']:>12,} filas")

    print("\nListo. Pegá esta salida en el chat para decidir qué índice/optimización toca.")


if __name__ == "__main__":
    main()
