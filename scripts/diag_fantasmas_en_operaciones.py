"""diag_fantasmas_en_operaciones.py — READ-ONLY: ¿los boletos anulados que ya
confirmamos en `operaciones.negocio_movimientos` están TAMBIÉN en
`operaciones.operaciones` (la vista MOVIMIENTOS / detalle de cuenta)?

PORQUÉ: `jobs/operaciones_informes.py` upsertea por `boleto` y NUNCA borra —
mismo patrón que `jobs/negocio_movimientos.py`. Si Aunesa anula un boleto, la
fila queda fosilizada acá también. El user afirma haberlos VISTO en el detalle
de cuenta. Esto lo mide en vez de suponerlo (REGLA #2).

MÉTODO: el detector de fantasmas (validado 24/24 contra la API de Aunesa +
2 casos confirmados a mano por back office) sólo funciona sobre
`negocio_movimientos`, porque ahí `ingestado_en` es un timestamp por FECHA.
En `operaciones.operaciones` el timestamp es por lote (varias fechas) y además
hay cargas manuales de Excel → no sirve. Entonces se usa la lista de fantasmas
de negocio como sonda y se busca cada uno en operaciones, cruzando por los
DÍGITOS del comprobante ('BOL 2026059191' → '2026059191'), que es robusto a
cómo venga prefijado el campo en cada tabla.

Uso:
    python -m scripts.diag_fantasmas_en_operaciones            # YTD
    python -m scripts.diag_fantasmas_en_operaciones --desde 2026-01-01
    python -m scripts.diag_fantasmas_en_operaciones --full

No escribe nada (sólo SELECT). No pega a Aunesa.
"""
from __future__ import annotations

import argparse
from datetime import date

from psycopg.rows import dict_row

from core.postgres import get_pool

# Casos confirmados a mano por el user contra la app de Aunesa (2026-08-04):
# back office los anuló, no existen en el sistema contable.
_VALIDADOS = (
    ("102", date(2026, 8, 3),  "DOC 2026003497", "Solicitud susc FCI 11.000 MM ARS"),
    ("102", date(2026, 8, 3),  "DOC 2026003504", "Solicitud susc FCI 11.000 MM ARS"),
    ("101", date(2026, 4, 20), "BOL 2026059191", "Caución tomadora 9.549 MM (se rehizo en 2)"),
)

# Mismo CTE que scripts/diag_boletos_fantasma.py (detector validado).
_CTE = """
WITH ult AS (
    SELECT fecha, max(ingestado_en) AS ult_ing, count(DISTINCT ingestado_en) AS n_corridas
      FROM operaciones.negocio_movimientos
     WHERE fecha >= %(desde)s
     GROUP BY fecha
), marcadas AS (
    SELECT nm.*, (u.n_corridas > 1 AND nm.ingestado_en < u.ult_ing) AS fantasma
      FROM operaciones.negocio_movimientos nm
      JOIN ult u USING (fecha)
     WHERE nm.fecha >= %(desde)s
)
"""

# Normalización de comprobante/boleto a sólo dígitos.
_DIG = r"regexp_replace({0}, '\D', '', 'g')"

# Pesificación de `operaciones.bruto` (ARS directo; el resto × mep del boleto).
_PESIF_OPS = ("CASE WHEN moneda = 'ARS' THEN abs(COALESCE(bruto, 0)) "
              "ELSE abs(COALESCE(bruto, 0)) * COALESCE(mep, 0) END")


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def _plata(v: float | None) -> str:
    """Formato con la MISMA escala que el frontend: MM = miles de millones."""
    if v is None:
        return "—"
    a = abs(v)
    for corte, suf in ((1e12, "B"), (1e9, "MM"), (1e6, "M"), (1e3, "k")):
        if a >= corte:
            return f"${v / corte:,.2f} {suf}".replace(",", "@").replace(".", ",").replace("@", ".")
    return f"${v:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def _ar(v: float | None, dec: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:,.{dec}f}".replace(",", "@").replace(".", ",").replace("@", ".")


def _sep(titulo: str) -> None:
    print("\n" + "=" * 78)
    print(f"  {titulo}")
    print("=" * 78)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--desde", default=f"{date.today().year}-01-01", help="YYYY-MM-DD (default: YTD)")
    p.add_argument("--full", action="store_true", help="toda la historia")
    args = p.parse_args()

    desde = date(2000, 1, 1) if args.full else date.fromisoformat(args.desde)
    par = {"desde": desde}
    print(f"Ventana: desde {desde.isoformat()}")

    # ── 0. ¿Cómo viene escrito el boleto en cada tabla? ──────────────────────
    _sep("0. FORMATO DEL IDENTIFICADOR EN CADA TABLA")
    ej_nm = _q("SELECT comprobante FROM operaciones.negocio_movimientos "
               "WHERE comprobante IS NOT NULL ORDER BY fecha DESC LIMIT 5")
    ej_op = _q("SELECT boleto FROM operaciones.operaciones "
               "WHERE boleto IS NOT NULL ORDER BY concertacion DESC LIMIT 5")
    print("  negocio_movimientos.comprobante:")
    for r in ej_nm:
        print(f"      {r['comprobante']!r}")
    print("  operaciones.operaciones.boleto:")
    for r in ej_op:
        print(f"      {r['boleto']!r}")
    print("\n  → el cruce se hace por DÍGITOS, así que el prefijo no importa.")

    # ── 1. Los casos que el user validó a mano ──────────────────────────────
    _sep("1. LOS CASOS CONFIRMADOS POR BACK OFFICE — ¿están en operaciones?")
    print("  Estos 3 boletos NO existen en Aunesa (verificado a mano).")
    print("  Si aparecen abajo, están fosilizados en la vista MOVIMIENTOS.\n")
    for cta, fch, comp, desc in _VALIDADOS:
        dig = "".join(c for c in comp if c.isdigit())
        filas = _q(
            "SELECT boleto, concertacion, id_cuenta, denominacion, tipo_operacion, "
            "       instrumento, moneda, bruto, arancel, es_cierre, etapa, ingestado_en "
            "  FROM operaciones.operaciones "
            f" WHERE {_DIG.format('boleto')} = %(dig)s",
            {"dig": dig})
        print(f"  ── [{cta}] {fch.isoformat()} · {comp}")
        print(f"     {desc}")
        if not filas:
            print("     ✅ NO está en operaciones.operaciones — esa vista está limpia.\n")
            continue
        print(f"     ❌ ESTÁ en operaciones.operaciones ({len(filas)} fila/s):")
        for f in filas:
            print(f"        boleto={f['boleto']!r}  conc={f['concertacion']}  "
                  f"cta={f['id_cuenta']}")
            print(f"        {f['tipo_operacion']} · {f['instrumento']} · {f['moneda']}  "
                  f"bruto={_ar(f['bruto'])}  arancel={_ar(f['arancel'])}")
            print(f"        es_cierre={f['es_cierre']}  etapa={f['etapa']!r}  "
                  f"ingestado_en={f['ingestado_en']}")
        print()

    # ── 2. El día completo de esas cuentas, como lo ve la vista ─────────────
    _sep("2. EL DÍA COMPLETO EN operaciones.operaciones (para comparar en la app)")
    for cta, fch in dict.fromkeys((v[0], v[1]) for v in _VALIDADOS):
        filas = _q(
            "SELECT boleto, tipo_operacion, instrumento, moneda, bruto, arancel, "
            "       es_cierre, etapa "
            "  FROM operaciones.operaciones "
            " WHERE id_cuenta = %(cta)s AND concertacion = %(f)s "
            " ORDER BY boleto",
            {"cta": cta, "f": fch})
        fantasmas = {"".join(c for c in v[2] if c.isdigit())
                     for v in _VALIDADOS if v[0] == cta and v[1] == fch}
        print(f"\n  ── CUENTA {cta} · {fch.isoformat()} · {len(filas)} boleto(s)")
        if not filas:
            print("     (sin boletos)")
            continue
        for f in filas:
            dig = "".join(c for c in (f["boleto"] or "") if c.isdigit())
            marca = "  ❌ ANULADO EN AUNESA" if dig in fantasmas else ""
            print(f"     {f['boleto']:<18} {(f['tipo_operacion'] or '—')[:22]:<22} "
                  f"{(f['instrumento'] or '—')[:12]:<12} {f['moneda'] or '—':<5} "
                  f"{_ar(f['bruto']):>22}{marca}")

    # ── 3. Cuántos fantasmas de negocio están también en operaciones ────────
    _sep("3. MEDICIÓN GLOBAL — fantasmas detectados que llegaron a operaciones")
    tot = _q(_CTE + """
        SELECT count(*) FILTER (WHERE fantasma)                            AS n_fantasmas,
               count(*) FILTER (WHERE fantasma AND comprobante LIKE 'BOL%') AS n_fant_bol
          FROM marcadas
    """, par)[0]
    print(f"  Fantasmas detectados en negocio_movimientos : {tot['n_fantasmas']:,}"
          .replace(",", "."))
    print(f"  De ésos, con prefijo BOL (boletos de mercado): {tot['n_fant_bol']:,}"
          .replace(",", "."))
    print("  (los DOC/CL/CE son solicitudes y liquidaciones de FCI — no viajan por")
    print("   el endpoint de informes, así que no deberían estar en operaciones)\n")

    cruce = _q(_CTE + f"""
        , fant AS (
            SELECT DISTINCT {_DIG.format('comprobante')} AS dig, comprobante
              FROM marcadas WHERE fantasma
        )
        SELECT count(*)                     AS n_en_ops,
               sum({_PESIF_OPS})            AS bruto_falso,
               count(*) FILTER (WHERE o.es_cierre) AS n_cierres
          FROM operaciones.operaciones o
          JOIN fant f ON f.dig = {_DIG.format('o.boleto')}
         WHERE o.concertacion >= %(desde)s
    """, par)[0]
    n = cruce["n_en_ops"] or 0
    print(f"  ►► FANTASMAS PRESENTES EN operaciones.operaciones : {n:,}".replace(",", "."))
    print(f"     Bruto pesificado que aportan               : {_plata(cruce['bruto_falso'])}")
    print(f"     (de ésos, {cruce['n_cierres'] or 0} son cierres de caución)")
    if n == 0:
        print("\n  ✅ Ninguno llegó a operaciones — la vista MOVIMIENTOS está limpia.")
        print("     El problema sería SOLO de negocio_movimientos.")
    else:
        print("\n  ❌ CONFIRMADO: la vista MOVIMIENTOS también arrastra boletos anulados.")

    # ── 4. Peso sobre el volumen de la vista MOVIMIENTOS, por mes ───────────
    if n:
        _sep("4. PESO SOBRE EL VOLUMEN DE MOVIMIENTOS, POR MES")
        print("  (mismo filtro que la vista: es_cierre=false, etapa <> 'solicitud')\n")
        filas = _q(_CTE + f"""
            , fant AS (
                SELECT DISTINCT {_DIG.format('comprobante')} AS dig
                  FROM marcadas WHERE fantasma
            )
            SELECT to_char(o.concertacion, 'YYYY-MM')                       AS mes,
                   sum({_PESIF_OPS})                                        AS total,
                   sum({_PESIF_OPS}) FILTER (WHERE f.dig IS NOT NULL)       AS falso,
                   count(*) FILTER (WHERE f.dig IS NOT NULL)                AS n_falso
              FROM operaciones.operaciones o
              LEFT JOIN fant f ON f.dig = {_DIG.format('o.boleto')}
             WHERE o.concertacion >= %(desde)s
               AND COALESCE(o.es_cierre, false) = false
               AND COALESCE(o.etapa, '') <> 'solicitud'
             GROUP BY 1 ORDER BY 1
        """, par)
        print(f"  {'MES':<9} {'VOL. TOTAL':>16} {'VOL. FALSO':>16} {'%':>8} {'#':>6}")
        print("  " + "-" * 60)
        gt = gf = gn = 0.0
        for r in filas:
            t, fa = float(r["total"] or 0), float(r["falso"] or 0)
            gt += t
            gf += fa
            gn += r["n_falso"] or 0
            pct = (fa / t * 100) if t else 0.0
            print(f"  {r['mes']:<9} {_plata(t):>16} {_plata(fa):>16} "
                  f"{_ar(pct):>7}% {r['n_falso'] or 0:>6}")
        print("  " + "-" * 60)
        print(f"  {'TOTAL':<9} {_plata(gt):>16} {_plata(gf):>16} "
              f"{_ar(gf / gt * 100 if gt else 0):>7}% {int(gn):>6}")

        _sep("5. TOP CUENTAS AFECTADAS EN MOVIMIENTOS")
        filas = _q(_CTE + f"""
            , fant AS (
                SELECT DISTINCT {_DIG.format('comprobante')} AS dig
                  FROM marcadas WHERE fantasma
            )
            SELECT o.id_cuenta, max(o.denominacion) AS nom,
                   sum({_PESIF_OPS})                                  AS total,
                   sum({_PESIF_OPS}) FILTER (WHERE f.dig IS NOT NULL) AS falso,
                   count(*) FILTER (WHERE f.dig IS NOT NULL)          AS n_falso
              FROM operaciones.operaciones o
              LEFT JOIN fant f ON f.dig = {_DIG.format('o.boleto')}
             WHERE o.concertacion >= %(desde)s
               AND COALESCE(o.es_cierre, false) = false
               AND COALESCE(o.etapa, '') <> 'solicitud'
             GROUP BY o.id_cuenta
            HAVING count(*) FILTER (WHERE f.dig IS NOT NULL) > 0
             ORDER BY 4 DESC NULLS LAST LIMIT 15
        """, par)
        print(f"  {'CTA':<7} {'NOMBRE':<34} {'FALSO':>14} {'TOTAL':>14} {'%':>7} {'#':>4}")
        print("  " + "-" * 84)
        for r in filas:
            t, fa = float(r["total"] or 0), float(r["falso"] or 0)
            print(f"  {r['id_cuenta'] or '—':<7} {(r['nom'] or '—')[:34]:<34} "
                  f"{_plata(fa):>14} {_plata(t):>14} "
                  f"{_ar(fa / t * 100 if t else 0):>6}% {r['n_falso']:>4}")

    print("\n✅ diag terminado (read-only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
