"""READ-ONLY. ¿El día de `portafolio.tenencia` está COMPLETO?

Herramienta: diag · Contesta la pregunta que hoy el agente no sabe hacer.

EL PUNTO
========

`agente/rehacer.hay_dato()` pregunta `SELECT 1 FROM portafolio.tenencia WHERE
fecha = X LIMIT 1`. O sea: **¿escribió ALGUIEN?** Y este job no escribe «el
día»: escribe **una fila por cuenta**, ~1.800 por corrida, y cada cuenta puede
fallar por su cuenta (timeout, 500 de Aunesa).

Con esa pregunta, un día en el que escribieron 20 cuentas y fallaron 1.848 se
lee EXACTAMENTE IGUAL que uno completo — y el botón de rehacer contesta «ya
estaba, no hacía falta». Es el mismo modo de falla del 2026-08-07, cuando las
1.868 cuentas devolvieron 500 y el tablero siguió en verde dos días.

La respuesta buena ya existe y la escribe el propio job:
**`portafolio.backfill_log`**, una fila por (fecha, id_cuenta) con su status.

Uso:
    python -m scripts.diag_tenencia_dia            # el día que le tocaba al cron
    python -m scripts.diag_tenencia_dia 2026-08-27
"""
from __future__ import annotations

import sys

from core.postgres import get_pool


def _filas(sql: str, params: tuple = ()) -> list[tuple]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def main() -> int:
    fecha = sys.argv[1] if len(sys.argv) > 1 else ""
    if not fecha:
        from agente.rehacer import fecha_objetivo
        fecha = fecha_objetivo("portafolio_diario")
        print(f"(sin fecha: uso la que le tocaba al cron → {fecha})")
    if not fecha:
        print("no pude resolver la fecha objetivo")
        return 1

    print(f"\n{'=' * 74}\n DÍA {fecha} — portafolio.tenencia\n{'=' * 74}")

    # 1. Lo que ve el agente HOY: binario.
    hay = _filas("SELECT 1 FROM portafolio.tenencia WHERE fecha::date = %s LIMIT 1",
                 (fecha,))
    print(f"\n  `hay_dato()` (lo que decide el botón hoy) → "
          f"{'el día ESTÁ' if hay else 'el día FALTA'}")

    # 2. Lo que pasó de verdad, por cuenta.
    print("\n  EL LIBRO DEL JOB (portafolio.backfill_log) — una fila por cuenta:")
    estados = _filas(
        "SELECT status, count(*) FROM portafolio.backfill_log "
        " WHERE fecha::date = %s GROUP BY status ORDER BY count(*) DESC",
        (fecha,))
    if not estados:
        print("      (ninguna fila: el job no dejó rastro de ese día)")
    total = sum(n for _, n in estados)
    for st, n in estados:
        # `ok` y `vacia` son las dos que el job considera HECHAS
        # (`_ya_hechas`); el resto vuelve a intentarse en la próxima corrida.
        marca = "✔" if st in ("ok", "vacia") else "✗ SE REINTENTA"
        print(f"      {marca:16} {st:24} {n:>6}")
    hechas = sum(n for st, n in estados if st in ("ok", "vacia"))
    if total:
        print(f"\n      completas: {hechas}/{total} ({hechas / total:.1%})")

    # 3. Y las filas que realmente quedaron.
    n_filas, n_cuentas = _filas(
        "SELECT count(*), count(DISTINCT id_cuenta) FROM portafolio.tenencia "
        " WHERE fecha::date = %s", (fecha,))[0]
    print(f"\n  FILAS EN LA TABLA: {n_filas:,} · cuentas distintas: {n_cuentas:,}")

    # 4. Para tener con qué comparar: los últimos días hábiles.
    print("\n  LOS ÚLTIMOS 7 DÍAS CARGADOS (para ver si este se sale de la norma):")
    for f, nf, nc in _filas(
            "SELECT fecha, count(*), count(DISTINCT id_cuenta) "
            "  FROM portafolio.tenencia GROUP BY fecha "
            " ORDER BY fecha DESC LIMIT 7"):
        aca = "  ← ESTE" if str(f) == str(fecha) else ""
        print(f"      {f}  filas={nf:>8,}  cuentas={nc:>6,}{aca}")

    print("\n  → Si «el día ESTÁ» pero las completas NO son el 100%, el botón de "
          "rehacer\n    contestaría «ya estaba» sobre un día a medias. Ese es el "
          "número que\n    decide si la prueba del job tiene que ser el LIBRO y "
          "no la tabla.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
