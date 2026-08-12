"""diag_acreencias_clasificacion.py — ¿qué eventos corporativos NO estamos
clasificando como acreencia? (read-only).

CONTEXTO. Hasta el 2026-08-12 el filtro de acreencia era una lista CERRADA de 3
substrings exactos (cash dividend / interest payment / partial redemption), así
que "Stock dividend (DVSE)" caía en `categoria='otro'` con `op=NULL`. Ahora
`aunesa_negocio.ACREENCIA_OPS` usa criterio AMPLIO por familia ("dividend" ⇒
dividendo, "redemption" ⇒ rescate).

Este diag sigue siendo útil para lo mismo de siempre: ver qué eventos
corporativos quedan FUERA del criterio vigente, y si los que entran mueven
plata o nominales.

Aunesa etiqueta el evento con su código CAEV entre paréntesis (ISO 15022):
DVCA=Cash dividend, INTR=Interest payment, PRED=Partial redemption,
DVSE=Stock dividend, BONU=Bonus issue, SPLF=Stock split, etc. Este diag extrae
ESE código del texto y mide, por código:

  · cuántas filas / cuentas / tickers hay y en qué categoría caen hoy,
  · si el evento mueve PLATA (importe≠0) o NOMINALES (cantidad≠0) — que es
    la pregunta que decide si entra al PnL pasivo o al cost-basis,
  · el importe por moneda y la cantidad total.

Lo que queda pendiente y este diag ayuda a dimensionar: los eventos que mueven
NOMINALES no ajustan el cost-basis por la vía de `acreencia` (la rama del motor
ignora `cantidad` a propósito). Para esos hace falta un `ajuste_cantidad` en
`operaciones.pnl_ajustes`. Las columnas 'c/importe' y 'c/cant' dicen cuáles son.

Uso (Droplet):
    python -m scripts.diag_acreencias_clasificacion             # últimos 365 días
    python -m scripts.diag_acreencias_clasificacion --dias 90
    python -m scripts.diag_acreencias_clasificacion --dias 0    # histórico completo

Read-only: solo SELECT sobre operaciones.negocio_movimientos.
"""
from __future__ import annotations

import argparse

from api.services.aunesa_negocio import op_acreencia
from core.postgres import get_pool

# Códigos CAEV que el criterio VIGENTE ya clasifica como acreencia. No se
# listan a mano: se derivan preguntándole a `op_acreencia` por el nombre del
# evento, así el diag no puede quedar desfasado del categorizador real.
_CAEV_CONOCIDOS = {
    "DVCA": "Cash dividend",   "INTR": "Interest payment",
    "PRED": "Partial redemption", "DVSE": "Stock dividend",
    "DVOP": "Optional dividend",  "REDM": "Final redemption",
    "BONU": "Bonus issue",        "SPLF": "Stock split",
    "EXOF": "Exchange offer",     "MRGR": "Merger",
}
CAEV_MAPEADOS = {c: n for c, n in _CAEV_CONOCIDOS.items() if op_acreencia(n)}

# Código CAEV = 4 mayúsculas entre paréntesis, ej "Stock dividend (DVSE)".
_CAEV = r"\(([A-Z]{4})\)"


def _where_fecha(dias: int) -> tuple[str, dict]:
    if dias <= 0:
        return "", {}
    return " AND fecha >= (CURRENT_DATE - %(dias)s::int)", {"dias": dias}


def _por_codigo(dias: int) -> None:
    print("=" * 96)
    print("1) EVENTOS CORPORATIVOS por código CAEV × categoría actual")
    print("=" * 96)
    wf, p = _where_fecha(dias)
    sql = f"""
        SELECT substring(informacion from %(caev)s)        AS caev,
               categoria,
               count(*)                                    AS n,
               count(DISTINCT id_cuenta)                   AS n_cuentas,
               count(DISTINCT ticker)                      AS n_tickers,
               count(*) FILTER (WHERE coalesce(importe, 0)  <> 0) AS con_importe,
               count(*) FILTER (WHERE coalesce(cantidad, 0) <> 0) AS con_cantidad,
               min(fecha)                                  AS desde,
               max(fecha)                                  AS hasta
        FROM operaciones.negocio_movimientos
        WHERE informacion ~ %(caev)s {wf}
        GROUP BY 1, 2
        ORDER BY n DESC
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"caev": _CAEV, **p})
        rows = cur.fetchall()
    if not rows:
        print("  (sin filas con código CAEV en `informacion` en la ventana pedida)")
        return
    print(f"  {'CAEV':<6}{'categoria':<14}{'n':>7}{'cuentas':>9}{'tickers':>9}"
          f"{'c/importe':>11}{'c/cant':>8}  {'desde':<12}{'hasta':<12} mapeado")
    for caev, cat, n, nc, nt, ci, cc, d1, d2 in rows:
        mark = "SI" if caev in CAEV_MAPEADOS else "-- NO"
        print(f"  {caev or '?':<6}{(cat or '?'):<14}{n:>7}{nc:>9}{nt:>9}"
              f"{ci:>11}{cc:>8}  {d1!s:<12}{d2!s:<12} {mark}")
    print("\n  LECTURA: 'c/importe' vs 'c/cant' dice si el evento movió PLATA o")
    print("  NOMINALES. Un evento sin importe NO es un cobro — no puede entrar a")
    print("  pnl_pasivo aunque conceptualmente sea 'algo que el cliente recibe'.")


def _plata_por_codigo(dias: int) -> None:
    print()
    print("=" * 96)
    print("2) MONTOS por código CAEV × moneda (solo los que NO son acreencia hoy)")
    print("=" * 96)
    wf, p = _where_fecha(dias)
    sql = f"""
        SELECT substring(informacion from %(caev)s) AS caev,
               coalesce(moneda, '?')                AS moneda,
               count(*)                             AS n,
               sum(coalesce(importe, 0))            AS importe,
               sum(coalesce(cantidad, 0))           AS cantidad
        FROM operaciones.negocio_movimientos
        WHERE informacion ~ %(caev)s
          AND coalesce(categoria, '') <> 'acreencia' {wf}
        GROUP BY 1, 2
        ORDER BY n DESC
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"caev": _CAEV, **p})
        rows = cur.fetchall()
    if not rows:
        print("  (nada fuera de acreencia — el filtro actual cubre todo)")
        return
    print(f"  {'CAEV':<6}{'mon':<5}{'n':>7}{'Σ importe':>20}{'Σ cantidad':>20}")
    for caev, mon, n, imp, cant in rows:
        print(f"  {caev or '?':<6}{mon:<5}{n:>7}{float(imp or 0):>20,.2f}"
              f"{float(cant or 0):>20,.2f}")


def _muestra(dias: int, limite: int) -> None:
    print()
    print("=" * 96)
    print(f"3) MUESTRA de filas con CAEV no mapeado (máx {limite})")
    print("=" * 96)
    wf, p = _where_fecha(dias)
    sql = f"""
        SELECT fecha, categoria, op, ticker, moneda, importe, cantidad, informacion
        FROM operaciones.negocio_movimientos
        WHERE informacion ~ %(caev)s
          AND coalesce(substring(informacion from %(caev)s), '') <> ALL(%(ok)s)
          {wf}
        ORDER BY fecha DESC
        LIMIT %(lim)s
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"caev": _CAEV, "ok": list(CAEV_MAPEADOS), "lim": limite, **p})
        rows = cur.fetchall()
    if not rows:
        print("  (sin filas)")
        return
    for f, cat, op, tk, mon, imp, cant, info in rows:
        print(f"  {f!s:<12}{(cat or '?'):<8}{(op or 'NULL'):<22}{(tk or '?'):<8}"
              f"{(mon or '?'):<5}{float(imp or 0):>16,.2f}{float(cant or 0):>14,.2f}"
              f"  {(info or '')[:60]}")


def _dividend_libre(dias: int) -> None:
    """Red de seguridad: filas que dicen 'dividend' pero SIN código CAEV
    (si Aunesa manda alguna variante sin paréntesis, acá aparece)."""
    print()
    print("=" * 96)
    print("4) Filas con 'dividend' SIN código CAEV (variantes fuera del patrón)")
    print("=" * 96)
    wf, p = _where_fecha(dias)
    sql = f"""
        SELECT categoria, count(*) AS n, min(informacion) AS ejemplo
        FROM operaciones.negocio_movimientos
        WHERE informacion ILIKE '%%dividend%%'
          AND informacion !~ %(caev)s {wf}
        GROUP BY 1
        ORDER BY n DESC
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"caev": _CAEV, **p})
        rows = cur.fetchall()
    if not rows:
        print("  (ninguna — todas las variantes traen su código CAEV)")
        return
    for cat, n, ej in rows:
        print(f"  {(cat or '?'):<14}{n:>7}  {(ej or '')[:70]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=365,
                    help="ventana hacia atrás (0 = histórico completo). Default 365.")
    ap.add_argument("--muestra", type=int, default=25, help="filas de ejemplo. Default 25.")
    args = ap.parse_args()

    ventana = "histórico completo" if args.dias <= 0 else f"últimos {args.dias} días"
    print(f"\nDIAG clasificación de acreencias — operaciones.negocio_movimientos ({ventana})\n")
    _por_codigo(args.dias)
    _plata_por_codigo(args.dias)
    _muestra(args.dias, args.muestra)
    _dividend_libre(args.dias)
    print("\nListo (read-only).")


if __name__ == "__main__":
    main()
