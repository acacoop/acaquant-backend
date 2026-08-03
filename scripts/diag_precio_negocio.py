"""diag_precio_negocio.py — READ-ONLY: ¿por qué `precio` viene vacío al cruzar MAV?

`scripts.validacion_cruzada_mav` cruzó 433 boletos MAV, 431 encontraron su fila en
`operaciones.negocio_movimientos`… y `precio` salió NULL en el 100%. El cruce anda;
el dato no está donde se lo esperaba. Este diag responde CUÁL de estas es la causa,
sin adivinar:

  A) La columna `precio` de la base real no es la del esquema del repo (drift).
  B) `precio` está NULL para TODA la tabla (nadie lo escribe).
  C) `precio` se puebla en general, pero NO para las filas que matchean MAV
     (p. ej. porque esas filas son de una `categoria` que no lleva precio).
  D) El match es espurio: la fila existe pero viene vacía en todo lo demás.

Uso:
    python -m scripts.diag_precio_negocio
    python -m scripts.diag_precio_negocio --mercado MAV

No escribe nada (sólo SELECT).
"""
from __future__ import annotations

import argparse

from psycopg.rows import dict_row

from core.postgres import get_pool
from scripts.validacion_cruzada_mav import _resolver_mercado


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def _titulo(n: int, txt: str) -> None:
    print(f"\n{'─' * 72}\n{n}. {txt}\n{'─' * 72}")


def _columnas_reales() -> list[str]:
    """(A) Las columnas que EXISTEN de verdad. sql/schema.sql no siempre está
    aplicado 100% en la base real (advertencia del CLAUDE.md), así que se
    pregunta al catálogo en vez de confiar en el archivo."""
    _titulo(1, "Columnas REALES de operaciones.negocio_movimientos")
    rows = _q(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'operaciones' AND table_name = 'negocio_movimientos' "
        "ORDER BY ordinal_position"
    )
    if not rows:
        print("  ❌ La tabla no existe o no es visible con este usuario.")
        return []
    nombres = [r["column_name"] for r in rows]
    for r in rows:
        marca = " ←" if "precio" in r["column_name"].lower() else ""
        print(f"     {r['column_name']:<22} {r['data_type']}{marca}")
    candidatas = [c for c in nombres if "precio" in c.lower() or "price" in c.lower()]
    if not candidatas:
        print("\n  ❌ NO hay ninguna columna de precio. El dato no vive en esta tabla.")
    elif candidatas != ["precio"]:
        print(f"\n  ⚠️  Columnas de precio encontradas: {candidatas} — el script de")
        print("      validación usa 'precio'. Si el nombre real es otro, hay que ajustarlo.")
    return nombres


def _cobertura_global() -> None:
    """(B) ¿`precio` está poblado en la tabla, en general?"""
    _titulo(2, "Cobertura de `precio` en TODA la tabla")
    r = _q(
        "SELECT count(*) AS n, count(precio) AS con_precio, "
        "       count(*) FILTER (WHERE precio = 0) AS en_cero "
        "FROM operaciones.negocio_movimientos"
    )[0]
    n = r["n"] or 0
    if not n:
        print("  La tabla está vacía.")
        return
    pct = 100.0 * (r["con_precio"] or 0) / n
    print(f"     filas totales      {n:>12,}")
    print(f"     con precio         {r['con_precio']:>12,}  ({pct:.1f}%)")
    print(f"     precio = 0         {r['en_cero']:>12,}")
    if pct == 0:
        print("\n  → CAUSA B: `precio` está NULL en toda la tabla. Nadie lo escribe.")
    else:
        print(f"\n  → `precio` SÍ se puebla ({pct:.1f}%). Entonces el problema es")
        print("     específico de las filas que matchean MAV (ver punto 4).")


def _cobertura_por_categoria() -> None:
    _titulo(3, "Cobertura de `precio` por categoria (top 15)")
    rows = _q(
        "SELECT COALESCE(categoria,'(null)') AS categoria, count(*) AS n, "
        "       count(precio) AS con_precio "
        "FROM operaciones.negocio_movimientos "
        "GROUP BY 1 ORDER BY n DESC LIMIT 15"
    )
    print(f"     {'categoria':<26}{'filas':>10}{'con precio':>13}{'%':>8}")
    for r in rows:
        pct = 100.0 * (r["con_precio"] or 0) / (r["n"] or 1)
        print(f"     {r['categoria']:<26}{r['n']:>10,}{r['con_precio']:>13,}{pct:>7.0f}%")


def _filas_que_matchean(mercados: list[str]) -> None:
    """(C)/(D) Qué traen REALMENTE las filas de negocio que matchean un boleto MAV."""
    _titulo(4, f"Las filas de negocio que matchean boletos {mercados}")
    p = {"mercados": mercados}
    base = (
        "FROM operaciones.operaciones o "
        "JOIN operaciones.negocio_movimientos n ON n.comprobante = o.boleto "
        "WHERE o.mercado = ANY(%(mercados)s)"
    )

    r = _q(
        f"SELECT count(*) AS n, count(n.precio) AS con_precio, "
        f"count(n.cantidad) AS con_cantidad, count(n.importe) AS con_importe, "
        f"count(n.ticker) AS con_ticker {base}", p,
    )[0]
    n = r["n"] or 0
    if not n:
        print("  Ningún boleto de ese mercado matchea. Nada que analizar acá.")
        return
    print(f"     filas matcheadas   {n:>10,}")
    print(f"     · con precio       {r['con_precio']:>10,}")
    print(f"     · con cantidad     {r['con_cantidad']:>10,}")
    print(f"     · con importe      {r['con_importe']:>10,}")
    print(f"     · con ticker       {r['con_ticker']:>10,}")
    if not r["con_precio"] and (r["con_importe"] or r["con_cantidad"]):
        print("\n  → CAUSA C: la fila es REAL (trae importe/cantidad) pero `precio`")
        print("     viene vacío justo para estos boletos.")
    elif not any((r["con_precio"], r["con_importe"], r["con_cantidad"], r["con_ticker"])):
        print("\n  → CAUSA D: la fila matchea pero viene vacía en todo. Match espurio.")

    print("\n     Desglose por categoria:")
    for row in _q(
        f"SELECT COALESCE(n.categoria,'(null)') AS categoria, count(*) AS c, "
        f"count(n.precio) AS con_precio {base} GROUP BY 1 ORDER BY c DESC LIMIT 10", p,
    ):
        print(f"       {row['categoria']:<24}{row['c']:>8,}  con precio: {row['con_precio']:,}")

    print("\n     Muestra de 8 filas (lo que hay del lado de negocio):")
    for row in _q(
        f"SELECT o.boleto, n.fecha, n.categoria, n.op, n.ticker, n.cantidad, "
        f"n.precio, n.importe, n.moneda, n.estado {base} "
        f"ORDER BY n.fecha DESC LIMIT 8", p,
    ):
        print(f"       boleto={row['boleto']} fecha={row['fecha']} cat={row['categoria']!r} "
              f"op={row['op']!r} ticker={row['ticker']!r}")
        print(f"         cantidad={row['cantidad']} precio={row['precio']} "
              f"importe={row['importe']} moneda={row['moneda']} estado={row['estado']!r}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Por qué `precio` viene vacío al cruzar MAV.")
    ap.add_argument("--mercado", default=None, help="valor EXACTO de `mercado` (default: los que contengan MAV)")
    args = ap.parse_args()

    mercados = _resolver_mercado(args.mercado)
    cols = _columnas_reales()
    if "precio" not in cols:
        print("\n⚠️  Sin columna `precio` no tiene sentido seguir: el resto del diag la usa.")
        return
    _cobertura_global()
    _cobertura_por_categoria()
    _filas_que_matchean(mercados)
    print(f"\n{'─' * 72}\nMandale esta salida completa a Claude para decidir el paso siguiente.\n")


if __name__ == "__main__":
    main()
