"""diag_boletos_fantasma.py — READ-ONLY: ¿cuánto volumen FALSO hay en
`operaciones.negocio_movimientos` por boletos que Aunesa anuló y nosotros
nunca borramos?

CONTEXTO (verificado 2026-08-04 con scripts/diag_fci_negocio --aunesa):
Aunesa a veces carga un boleto mal, lo ANULA y emite uno nuevo corregido.
Nuestro job hace upsert por (fecha, comprobante) y NUNCA borra → el boleto
anulado queda pegado para siempre. Caso testigo: BOL 2026127024 (cuenta 199,
03/08) con USD 275.000.000 cuando la operación real fue USD 275.000. Ese día
había 24 comprobantes en nuestra base que Aunesa ya no devuelve, varios de
miles de millones de ARS en las cuentas 101/102/105.

MÉTODO (sin volver a pegarle a Aunesa):
`jobs/negocio_movimientos.py` es el ÚNICO escritor de `ingestado_en` y lo
refresca en cada corrida (`ingestado_en=EXCLUDED.ingestado_en`) con UN SOLO
timestamp por corrida. El cron re-visita cada fecha durante `_LOOKBACK_HABILES`
días hábiles. Entonces, dentro de una misma `fecha`, todas las filas que Aunesa
SIGUE devolviendo comparten el `max(ingestado_en)` de esa fecha; las que quedaron
con un timestamp ANTERIOR son boletos que Aunesa dejó de devolver = FANTASMAS.

LÍMITES (el número que sale es un PISO, no el total):
  - Sólo detecta anulaciones ocurridas DENTRO de la ventana de lookback. Si
    Aunesa anula 5 días después, esa fecha ya no se re-visita → falso negativo.
  - Una fecha ingerida UNA sola vez tiene un único `ingestado_en` → no auditable
    (sección 2 mide cuántas son).
  - No da falsos positivos: `aunesa_aranceles.py` (el otro writer) sólo toca
    `arancel`/`aranceles`, nunca `ingestado_en`.

Uso:
    python -m scripts.diag_boletos_fantasma                    # YTD
    python -m scripts.diag_boletos_fantasma --desde 2026-01-01
    python -m scripts.diag_boletos_fantasma --full             # toda la historia

No escribe nada (sólo SELECT). No pega a Aunesa.
"""
from __future__ import annotations

import argparse
from datetime import date

from psycopg.rows import dict_row

from core.postgres import get_pool

# = _CATS_VOLUMEN de api/services/comercial.py — las categorías que SUMAN al
# volumen del Tablero Comercial. Un fantasma fuera de esta lista ensucia la
# tabla pero no infla el volumen.
_CATS_VOLUMEN = (
    "compra", "venta",
    "suscripcion_fci", "solicitud_suscripcion_fci",
    "caucion_tom_ap", "caucion_col_ap",
)

# = _PESIF de comercial_sql.py (ARS directo; USD × mep del boleto).
_PESIF = ("CASE WHEN moneda = 'ARS' THEN abs(COALESCE(importe, 0)) "
          "ELSE abs(COALESCE(importe, 0)) * COALESCE(mep, 0) END")

# CTE base: marca cada fila como fantasma comparando contra el último
# `ingestado_en` de SU fecha.
_CTE = """
WITH ult AS (
    SELECT fecha, max(ingestado_en) AS ult_ing, count(DISTINCT ingestado_en) AS n_corridas
      FROM operaciones.negocio_movimientos
     WHERE fecha >= %(desde)s
     GROUP BY fecha
), marcadas AS (
    SELECT nm.*, u.ult_ing, u.n_corridas,
           (u.n_corridas > 1 AND nm.ingestado_en < u.ult_ing) AS fantasma
      FROM operaciones.negocio_movimientos nm
      JOIN ult u USING (fecha)
     WHERE nm.fecha >= %(desde)s
)
"""


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def _titulo(n: int, txt: str) -> None:
    print(f"\n{'─' * 78}\n{n}. {txt}\n{'─' * 78}")


def _plata(x) -> str:
    """Formato AR con sufijo de magnitud — para leer miles de millones de un vistazo."""
    if x is None:
        return "—"
    v = float(x)
    for lim, suf in ((1e12, "B"), (1e9, "MM"), (1e6, "M"), (1e3, "k")):
        if abs(v) >= lim:
            return f"${v / lim:,.2f} {suf}".replace(",", "@").replace(".", ",").replace("@", ".")
    return f"${v:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


# ─── 0. Tamaño del problema ─────────────────────────────────────────────────


def _tamano(desde: date) -> None:
    _titulo(0, "Tamaño de la tabla y ventana analizada")
    r = _q(
        "SELECT count(*) AS n, min(fecha) AS f0, max(fecha) AS f1 "
        "  FROM operaciones.negocio_movimientos"
    )[0]
    print(f"  negocio_movimientos: {r['n']:,} filas · {r['f0']} → {r['f1']}".replace(",", "."))
    r2 = _q(
        "SELECT count(*) AS n FROM operaciones.negocio_movimientos WHERE fecha >= %(desde)s",
        {"desde": desde},
    )[0]
    print(f"  Ventana analizada (desde {desde}): {r2['n']:,} filas".replace(",", "."))


# ─── 1. Validación del método contra el caso testigo ────────────────────────


def _validar(dia: date) -> None:
    _titulo(1, f"VALIDACIÓN del método — corridas sobre {dia}")
    rows = _q(
        "SELECT ingestado_en, count(*) AS n FROM operaciones.negocio_movimientos "
        " WHERE fecha = %(d)s GROUP BY ingestado_en ORDER BY ingestado_en",
        {"d": dia},
    )
    if not rows:
        print("  (sin filas para ese día)")
        return
    for i, r in enumerate(rows):
        marca = "  ← ÚLTIMA (boletos vivos)" if i == len(rows) - 1 else "  ← fantasmas"
        print(f"     {r['ingestado_en']}  {r['n']:>5} filas{marca}")
    fant = sum(r["n"] for r in rows[:-1])
    print(f"\n  → El método detecta {fant} fantasmas para {dia}.")
    print("     Aunesa confirmó 24 el 2026-08-03 (diag_fci_negocio --aunesa).")
    print("     Si coincide, el método es válido y se puede aplicar a toda la historia.")


# ─── 2. Cobertura: qué fechas son auditables ────────────────────────────────


def _cobertura(desde: date) -> None:
    _titulo(2, "Cobertura del método — fechas auditables vs. ciegas")
    rows = _q(
        "SELECT count(*) FILTER (WHERE n > 1) AS auditables, "
        "       count(*) FILTER (WHERE n = 1) AS ciegas, count(*) AS total FROM ("
        "  SELECT fecha, count(DISTINCT ingestado_en) AS n "
        "    FROM operaciones.negocio_movimientos WHERE fecha >= %(desde)s GROUP BY fecha) t",
        {"desde": desde},
    )[0]
    tot = rows["total"] or 1
    print(f"  Fechas con >1 corrida (AUDITABLES): {rows['auditables']} "
          f"({100 * rows['auditables'] / tot:.1f}%)")
    print(f"  Fechas con 1 sola corrida (CIEGAS): {rows['ciegas']} "
          f"({100 * rows['ciegas'] / tot:.1f}%)")
    print("  En las ciegas no podemos saber si hay fantasmas → el total real es MAYOR.")


# ─── 3. Stock de fantasmas por mes ──────────────────────────────────────────


def _por_mes(desde: date) -> None:
    _titulo(3, "Fantasmas por mes — cuántos y cuánto volumen FALSO aportan")
    rows = _q(
        _CTE + f"""
        SELECT date_trunc('month', fecha)::date AS mes,
               count(*) FILTER (WHERE fantasma) AS n_fant,
               count(*) AS n_total,
               SUM({_PESIF}) FILTER (
                   WHERE fantasma AND categoria = ANY(%(cats)s)) AS vol_falso,
               SUM({_PESIF}) FILTER (
                   WHERE categoria = ANY(%(cats)s)) AS vol_total
          FROM marcadas GROUP BY 1 ORDER BY 1
        """,
        {"desde": desde, "cats": list(_CATS_VOLUMEN)},
    )
    print(f"  {'MES':<12}{'FANTASMAS':>12}{'/ TOTAL':>10}{'VOL FALSO (ARS)':>22}"
          f"{'VOL TOTAL (ARS)':>22}{'% INFLADO':>12}")
    tf = tv = 0.0
    for r in rows:
        vf = float(r["vol_falso"] or 0)
        vt = float(r["vol_total"] or 0)
        tf += vf
        tv += vt
        pct = f"{100 * vf / vt:.2f}%" if vt else "—"
        print(f"  {r['mes']!s:<12}{r['n_fant']:>12}{r['n_total']:>10}"
              f"{_plata(vf):>22}{_plata(vt):>22}{pct:>12}")
    pct_t = f"{100 * tf / tv:.2f}%" if tv else "—"
    print(f"\n  TOTAL VOLUMEN FALSO: {_plata(tf)} sobre {_plata(tv)} → {pct_t} inflado")


# ─── 4. Fantasmas por categoría ─────────────────────────────────────────────


def _por_categoria(desde: date) -> None:
    _titulo(4, "Fantasmas por categoría — ¿cuáles pegan en el volumen comercial?")
    rows = _q(
        _CTE + f"""
        SELECT categoria, count(*) AS n, SUM({_PESIF}) AS vol
          FROM marcadas WHERE fantasma GROUP BY 1 ORDER BY 3 DESC NULLS LAST
        """,
        {"desde": desde},
    )
    for r in rows:
        suma = "SUMA al volumen" if r["categoria"] in _CATS_VOLUMEN else "no suma"
        print(f"     {r['categoria']!s:<28}{r['n']:>7} boletos  "
              f"{_plata(r['vol']):>20}   ({suma})")


# ─── 5. Top fantasmas por plata ─────────────────────────────────────────────


def _top(desde: date, n: int) -> None:
    _titulo(5, f"Top {n} fantasmas por importe pesificado")
    rows = _q(
        _CTE + f"""
        SELECT fecha, comprobante, id_cuenta, cuenta, categoria, ticker, moneda,
               importe, {_PESIF} AS pesif, ingestado_en, ult_ing
          FROM marcadas WHERE fantasma ORDER BY pesif DESC NULLS LAST LIMIT %(n)s
        """,
        {"desde": desde, "n": n},
    )
    for r in rows:
        print(f"\n     {r['fecha']}  {r['comprobante']}  cta={r['id_cuenta']} "
              f"{str(r['cuenta'] or '')[:52]}")
        print(f"        {r['categoria']!s:<26} {r['ticker']!s:<18} "
              f"importe={r['importe']} {r['moneda']}  → pesificado {_plata(r['pesif'])}")
        print(f"        ingestado_en={r['ingestado_en']}  (última corrida del día: {r['ult_ing']})")


# ─── 6. Impacto por cuenta ──────────────────────────────────────────────────


def _por_cuenta(desde: date, n: int) -> None:
    _titulo(6, f"Top {n} cuentas con volumen FALSO — a quién le estamos inflando el ranking")
    rows = _q(
        _CTE + f"""
        SELECT id_cuenta, max(cuenta) AS cuenta,
               count(*) FILTER (WHERE fantasma) AS n_fant,
               SUM({_PESIF}) FILTER (
                   WHERE fantasma AND categoria = ANY(%(cats)s)) AS vol_falso,
               SUM({_PESIF}) FILTER (
                   WHERE categoria = ANY(%(cats)s)) AS vol_total
          FROM marcadas GROUP BY id_cuenta
        HAVING SUM({_PESIF}) FILTER (
                   WHERE fantasma AND categoria = ANY(%(cats)s)) > 0
         ORDER BY vol_falso DESC LIMIT %(n)s
        """,
        {"desde": desde, "cats": list(_CATS_VOLUMEN), "n": n},
    )
    for r in rows:
        vf = float(r["vol_falso"] or 0)
        vt = float(r["vol_total"] or 0)
        pct = f"{100 * vf / vt:.1f}%" if vt else "—"
        print(f"     cta={r['id_cuenta']!s:<7} {str(r['cuenta'] or '')[:46]:<46} "
              f"{r['n_fant']:>4} fant  falso={_plata(vf):>16} de {_plata(vt):>16} ({pct})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--desde", default=None, help="YYYY-MM-DD (default: 1 de enero del año)")
    ap.add_argument("--full", action="store_true", help="toda la historia de la tabla")
    ap.add_argument("--dia-testigo", default="2026-08-03", help="día para validar el método")
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()

    if args.full:
        desde = date(1970, 1, 1)
    elif args.desde:
        desde = date.fromisoformat(args.desde)
    else:
        desde = date(date.today().year, 1, 1)

    _tamano(desde)
    _validar(date.fromisoformat(args.dia_testigo))
    _cobertura(desde)
    _por_mes(desde)
    _por_categoria(desde)
    _top(desde, args.top)
    _por_cuenta(desde, args.top)

    print("\n✅ diag terminado (read-only, sin pegarle a Aunesa).")


if __name__ == "__main__":
    main()
