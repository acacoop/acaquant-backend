"""fix_anulados_indebidos.py — revisa y repara las marcas de `anulado_en` que
la primera corrida de reconciliación puso MAL en `operaciones.operaciones`.

QUÉ PASÓ (2026-08-04): la reconciliación comparaba TODOS los boletos de un
(cuenta, día) contra lo que devolvió el endpoint de informes. Pero esa tabla la
escriben DOS fuentes: `jobs/operaciones_informes` (boletos 'BOL ') y
`jobs/fci_bilateral` (los 'CL '/'DOC ' del FCI bilateral). Los de la segunda
fuente nunca vienen por informes → se marcaban como anulados sin serlo.

El job ya está corregido (sólo audita los prefijos que Aunesa devolvió en la
corrida). Esto limpia lo que quedó marcado de más.

Uso:
    python -m scripts.fix_anulados_indebidos          # sólo reporta
    python -m scripts.fix_anulados_indebidos --fix    # desmarca los indebidos

Sin `--fix` no escribe nada.
"""
from __future__ import annotations

import argparse

from psycopg.rows import dict_row

from core.postgres import get_pool

# Prefijos que SÍ vienen del endpoint de informes → los únicos sobre los que la
# reconciliación tiene autoridad. El resto lo escriben otros jobs.
_PROPIOS = ("BOL",)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fix", action="store_true", help="desmarca los indebidos")
    args = p.parse_args()

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT split_part(boleto, ' ', 1) AS prefijo, count(*) AS n,
                   min(concertacion) AS desde, max(concertacion) AS hasta
              FROM operaciones WHERE anulado_en IS NOT NULL
             GROUP BY 1 ORDER BY 2 DESC
        """)
        por_prefijo = cur.fetchall()

        print("\n  MARCAS DE ANULACIÓN ACTUALES, POR PREFIJO")
        print("  " + "-" * 62)
        print(f"  {'PREFIJO':<10} {'#':>6}  {'RANGO':<26} VEREDICTO")
        total = malos = 0
        for r in por_prefijo:
            ok = r["prefijo"] in _PROPIOS
            total += r["n"]
            if not ok:
                malos += r["n"]
            print(f"  {r['prefijo']:<10} {r['n']:>6}  "
                  f"{r['desde']!s} .. {r['hasta']!s}   "
                  f"{'legítima' if ok else '❌ INDEBIDA (otra fuente)'}")
        print("  " + "-" * 62)
        print(f"  Total marcadas: {total}   ·   indebidas: {malos}")

        if not malos:
            print("\n  ✅ No hay marcas para revertir.")
            return 0

        cur.execute("""
            SELECT boleto, concertacion, id_cuenta, denominacion, tipo_operacion, bruto
              FROM operaciones
             WHERE anulado_en IS NOT NULL AND split_part(boleto, ' ', 1) <> ALL(%s)
             ORDER BY bruto DESC NULLS LAST LIMIT 30
        """, (list(_PROPIOS),))
        print("\n  LAS INDEBIDAS (top 30 por importe):")
        print("  " + "-" * 92)
        for r in cur.fetchall():
            print(f"  {r['concertacion']!s}  {r['id_cuenta'] or '—':<6} {r['boleto']:<17} "
                  f"{(r['tipo_operacion'] or '—')[:30]:<30} {r['bruto'] or 0:>20,.2f}")

        if not args.fix:
            print("\n  (sólo reporte — volvé a correr con --fix para desmarcarlas)")
            return 0

        cur.execute("""
            UPDATE operaciones SET anulado_en = NULL
             WHERE anulado_en IS NOT NULL AND split_part(boleto, ' ', 1) <> ALL(%s)
        """, (list(_PROPIOS),))
        n = cur.rowcount
        conn.commit()
        print(f"\n  ✅ Desmarcadas {n} fila(s). Las 'BOL' quedan como están.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
