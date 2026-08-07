"""Diag READ-ONLY de performance de la vista Tesorería.

Mide, contra la base y la API reales, dónde se va el tiempo de la vista:

  1) Cada fuente de la grilla BANCOS por separado (Aunesa incluida), para saber si
     el costo está en SQL o en la llamada HTTP.
  2) El request completo `/tesoreria/dia` — el que el front pollea cada 20s.
  3) El detalle de una celda (`saldo_final` es el caso caro: recalcula la grilla).
  4) EXPLAIN (ANALYZE, BUFFERS) de las queries del día, para ver si alguna hace
     Seq Scan sobre una tabla que ya creció.

No escribe nada: usa `email=""` para no marcar presencia y todos los EXPLAIN son
sobre SELECT. Correr con:

    python -m scripts.diag_tesoreria_perf              # hoy
    python -m scripts.diag_tesoreria_perf 2026-08-06   # una fecha puntual
"""
from __future__ import annotations

import sys
import time
from datetime import date

from api.services import tesoreria as t
from api.services._sql import _q

# Queries del path caliente, con el mismo texto que corre en producción.
EXPLAIN: list[tuple[str, str, dict]] = []


def _seg(nombre: str, fn):
    """Corre `fn` y reporta cuánto tardó + cuántas filas trajo."""
    t0 = time.perf_counter()
    try:
        r = fn()
        ms = (time.perf_counter() - t0) * 1000
        n = len(r) if hasattr(r, "__len__") else "—"
        print(f"  {ms:8.1f} ms  {nombre:<42} ({n} filas)")
        return ms
    except Exception as e:
        ms = (time.perf_counter() - t0) * 1000
        print(f"  {ms:8.1f} ms  {nombre:<42} ERROR: {type(e).__name__}: {e}")
        return ms


def fuentes(dia: date) -> None:
    print("\n── FUENTES de la grilla BANCOS (cada una por separado) ────────────────")
    total = 0.0
    total += _seg("Aunesa (HTTP, movimientos del día)", lambda: t.traer_crudas(dia, t.TODOS_ESTADOS))
    total += _seg("catálogo de cuentas (catalogo)", lambda: t.catalogo())
    total += _seg("catálogo completo (listar_cuentas)", lambda: t.listar_cuentas())
    total += _seg("saldos iniciales", lambda: t._saldos_dia(dia))
    total += _seg("exclusiones (destildados)", lambda: t._exclusiones_dia(dia))
    total += _seg("cheques recibidos finalizados", lambda: t.ingresos_echeq_dia(dia))
    total += _seg("cheques emitidos T-1", lambda: t._cheques_emitidos_t1_rows(dia))
    total += _seg("mercados + fci", lambda: t.mercados_por_banco(dia))
    total += _seg("banco a banco", lambda: t.banco_a_banco_por_banco(dia))
    total += _seg("registros manuales", lambda: t.registros_por_banco(dia))
    total += _seg("total rescate ACA Valores", lambda: t.totales_rescate(dia))
    total += _seg("conectados (presencia)", lambda: t.conectados())
    print(f"  {'-' * 66}\n  {total:8.1f} ms  SUMA de las fuentes")


def requests_(dia: date) -> None:
    print("\n── REQUESTS completos (lo que ve el usuario) ──────────────────────────")
    iso = dia.isoformat()
    # 3 corridas: la 1ra puede pagar warm-up de pool/DNS, interesa la mediana.
    for i in range(3):
        _seg(f"GET /tesoreria/dia  (corrida {i + 1}/3)",
             lambda: t.ingresos_egresos_dia(fecha=iso, email="")["cuentas"])
    _seg("GET /tesoreria/detalle  fila=ingresos",
         lambda: t._detalle_dia(dia))
    # `saldo_final` es el caso caro: recalcula la grilla ENTERA (Aunesa incluida)
    # antes de poder listar el detalle de una sola celda.
    cuentas = t.ingresos_egresos_dia(fecha=iso, email="")["cuentas"]
    if cuentas:
        c = cuentas[0]
        _seg(f"GET /tesoreria/detalle  fila=saldo_final ({c['cuenta_operativa']})",
             lambda: t.detalle_celda(fecha=iso, banco=c["cuenta_operativa"],
                                     unidad=c["unidad"], fila="saldo_final", email="")["items"])


def explain(dia: date) -> None:
    print("\n── EXPLAIN de las queries del día (buscando Seq Scan) ─────────────────")
    consultas: list[tuple[str, str, dict]] = [
        ("cheques recibidos del día",
         f"SELECT banco, unidad, SUM(importe) FROM {t._TABLA_CHEQUES} "
         "WHERE lado = 'recibido' AND estado = 'finalizado' "
         f"AND (creado_at AT TIME ZONE '{t._TZ_ART}')::date = %(d)s GROUP BY banco, unidad",
         {"d": dia}),
        ("cheques emitidos T-1",
         f"SELECT banco, unidad, COUNT(*), SUM(importe) FROM {t._TABLA_CHEQUES} "
         "WHERE lado = 'emitido' AND estado = 'emitido' "
         "AND fecha_pago IS NOT NULL AND fecha_pago < %(d)s GROUP BY banco, unidad",
         {"d": dia}),
        ("mercados del día",
         f"SELECT banco, unidad, tipo, SUM(importe) FROM {t._TABLA_MERCADOS} "
         "WHERE fecha = %(d)s GROUP BY banco, unidad, tipo", {"d": dia}),
        ("registros manuales del día",
         f"SELECT banco, unidad, sentido, SUM(importe) FROM {t._TABLA_REGISTROS} "
         "WHERE fecha = %(d)s GROUP BY banco, unidad, sentido", {"d": dia}),
        ("banco a banco del día",
         f"SELECT * FROM {t._TABLA_BB} WHERE fecha = %(d)s", {"d": dia}),
        ("exclusiones del día",
         f"SELECT * FROM {t._TABLA_EXCL} WHERE fecha = %(d)s", {"d": dia}),
    ]
    for nombre, sql, params in consultas:
        try:
            plan = _q(f"EXPLAIN (ANALYZE, BUFFERS) {sql}", params)
            lineas = [next(iter(r.values())) for r in plan]
            seq = [ln for ln in lineas if "Seq Scan" in ln]
            cab = lineas[0] if lineas else "—"
            tiempo = next((ln for ln in lineas if ln.strip().startswith("Execution Time")), "")
            marca = "  ⚠ SEQ SCAN" if seq else ""
            print(f"\n  {nombre}{marca}\n    {cab.strip()}\n    {tiempo.strip()}")
            for ln in seq:
                print(f"    ⚠ {ln.strip()}")
        except Exception as e:
            print(f"\n  {nombre}\n    ERROR: {type(e).__name__}: {e}")


def volumen() -> None:
    print("\n── VOLUMEN de las tablas (cuánto crecieron) ───────────────────────────")
    tablas = [t._TABLA_CHEQUES, t._TABLA_MERCADOS, t._TABLA_REGISTROS, t._TABLA_BB,
              t._TABLA_EXCL, t._TABLA_SNAPSHOTS, "operaciones.tesoreria_cuentas",
              "operaciones.tesoreria_saldos"]
    for tab in tablas:
        try:
            r = _q(f"SELECT COUNT(*) AS n, pg_size_pretty(pg_total_relation_size('{tab}')) AS peso "
                   f"FROM {tab}")
            print(f"  {r[0]['n']:>8} filas  {r[0]['peso']:>10}  {tab}")
        except Exception as e:
            print(f"  {'?':>8}              {tab}  ({type(e).__name__})")


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    dia = t._dia(arg)
    print(f"\n{'=' * 72}\nDIAG PERFORMANCE — Tesorería · día {dia.isoformat()}\n{'=' * 72}")
    volumen()
    fuentes(dia)
    requests_(dia)
    explain(dia)
    print("\nListo. Lo que importa: (a) cuánto pesa Aunesa vs SQL en las FUENTES,")
    print("(b) si /tesoreria/dia se va por encima de ~1s, (c) cualquier ⚠ SEQ SCAN.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
