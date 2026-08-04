"""diag_propagacion_anulados.py — ¿podemos propagar las anulaciones de
`negocio_movimientos` a `operaciones` en vez de re-correr el job de informes?

LA PREGUNTA: las dos tablas vienen de endpoints DISTINTOS de Aunesa
(/consolidadosGenerales y /informes). Si Aunesa anula un boleto, ¿lo dejan de
devolver LOS DOS? Si la respuesta es sí, el histórico se resuelve con un UPDATE
en vez de 6 corridas de ~1.974 cuentas.

EL EXPERIMENTO: el 03 y 04/08 las DOS tablas se reconciliaron por su cuenta y en
forma independiente. Cruzamos boleto contra boleto y miramos si coinciden.

    anulado en ambas   → de acuerdo (propagar es seguro)
    anulado solo en NM → informes SÍ lo sigue devolviendo → propagar sería un
                         falso positivo
    anulado solo en OP → NM lo sigue devolviendo → propagar se lo perdería

Read-only. No escribe nada.

Uso:
    python -m scripts.diag_propagacion_anulados
    python -m scripts.diag_propagacion_anulados --desde 2026-08-03 --hasta 2026-08-04
"""
from __future__ import annotations

import argparse

from psycopg.rows import dict_row

from core.postgres import get_pool

# Ventana donde AMBAS tablas ya fueron reconciliadas (ver docstring).
_DESDE, _HASTA = "2026-08-03", "2026-08-04"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--desde", default=_DESDE)
    ap.add_argument("--hasta", default=_HASTA)
    a = ap.parse_args()
    p = {"d": a.desde, "h": a.hasta}

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        print(f"\n  VENTANA: {a.desde} → {a.hasta} (ambas tablas reconciliadas)")

        cur.execute("""
            SELECT count(*) FILTER (WHERE anulado_en IS NOT NULL) AS anul,
                   count(*) AS total
              FROM negocio_movimientos WHERE fecha BETWEEN %(d)s AND %(h)s
        """, p)
        r = cur.fetchone()
        print(f"  negocio_movimientos : {r['anul']:>5} anulados de {r['total']} boletos")
        cur.execute("""
            SELECT count(*) FILTER (WHERE anulado_en IS NOT NULL) AS anul,
                   count(*) AS total
              FROM operaciones WHERE concertacion BETWEEN %(d)s AND %(h)s
        """, p)
        r = cur.fetchone()
        print(f"  operaciones         : {r['anul']:>5} anulados de {r['total']} boletos")

        cur.execute("""
            WITH v AS (
                SELECT comprobante, fecha, (anulado_en IS NOT NULL) AS anul
                  FROM negocio_movimientos WHERE fecha BETWEEN %(d)s AND %(h)s
            )
            SELECT split_part(o.boleto, ' ', 1) AS prefijo,
                   v.anul AS en_nm, (o.anulado_en IS NOT NULL) AS en_ops,
                   count(*) AS n, count(*) FILTER (WHERE v.fecha <> o.concertacion) AS dif_fecha
              FROM operaciones o JOIN v ON v.comprobante = o.boleto
             WHERE o.concertacion BETWEEN %(d)s AND %(h)s
             GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
        """, p)
        filas = cur.fetchall()

        print("\n  BOLETOS PRESENTES EN LAS DOS TABLAS")
        print("  " + "-" * 74)
        print(f"  {'PREFIJO':<9} {'ANUL. EN NM':<12} {'ANUL. EN OPS':<13} {'#':>6}  VEREDICTO")
        for f in filas:
            if f["en_nm"] and f["en_ops"]:
                v = "✅ de acuerdo"
            elif f["en_nm"]:
                v = "⚠️  solo NM — propagar lo marcaría"
            elif f["en_ops"]:
                v = "⚠️  solo OPS — propagar no lo alcanza"
            else:
                v = "vivo en las dos"
            extra = f"  (+{f['dif_fecha']} con fecha distinta)" if f["dif_fecha"] else ""
            print(f"  {f['prefijo']:<9} {str(f['en_nm']):<12} {str(f['en_ops']):<13} "
                  f"{f['n']:>6}  {v}{extra}")

        cur.execute("""
            WITH v AS (
                SELECT comprobante FROM negocio_movimientos
                 WHERE fecha BETWEEN %(d)s AND %(h)s AND anulado_en IS NOT NULL
            )
            SELECT count(*) AS n FROM v
             WHERE NOT EXISTS (SELECT 1 FROM operaciones o WHERE o.boleto = v.comprobante)
        """, p)
        print(f"\n  Anulados en NM que NO existen en operaciones: {cur.fetchone()['n']} "
              f"(no aplican — nada que propagar)")

        cur.execute("""
            SELECT o.boleto, o.concertacion, o.id_cuenta, o.tipo_operacion, o.bruto
              FROM operaciones o
              JOIN negocio_movimientos nm ON nm.comprobante = o.boleto
             WHERE o.concertacion BETWEEN %(d)s AND %(h)s
               AND nm.anulado_en IS NOT NULL AND o.anulado_en IS NULL
             ORDER BY o.bruto DESC NULLS LAST LIMIT 20
        """, p)
        des = cur.fetchall()
        if des:
            print("\n  LOS QUE NO COINCIDEN (anulado en NM, vivo en OPS) — top 20:")
            print("  " + "-" * 88)
            for r in des:
                print(f"  {r['concertacion']!s}  {r['id_cuenta'] or '—':<6} {r['boleto']:<17} "
                      f"{(r['tipo_operacion'] or '—')[:28]:<28} {r['bruto'] or 0:>18,.2f}")
        else:
            print("\n  ✅ Ningún desacuerdo: todo lo anulado en NM ya está anulado en OPS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
