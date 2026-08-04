"""diag_validacion_fantasmas.py — READ-ONLY: genera una LISTA DE VERIFICACIÓN
CRUZADA para chequear a mano contra el sistema contable (app de Aunesa).

Por qué: `scripts/diag_boletos_fantasma.py` detecta boletos anulados usando el
timestamp `ingestado_en` (método validado: 24/24 el 2026-08-03). Pero antes de
escribir un fix que MARQUE registros como anulados, conviene que un humano
confirme unos cuantos casos contra la fuente contable.

Cómo leer la salida: cada CASO trae DOS boletos de la MISMA cuenta y el MISMO
día:
  ❓ SOSPECHOSO — nuestra base lo tiene; creemos que Aunesa lo anuló.
                  ESPERADO: NO aparece en la app.
  ✅ CONTROL    — nuestra base lo tiene y Aunesa lo sigue devolviendo.
                  ESPERADO: SÍ aparece en la app.

El control es lo que hace la prueba concluyente: si el control aparece y el
sospechoso no, el método está bien. Si NO aparece ninguno de los dos, el
problema es dónde/cómo se buscó, no el dato.

La cuenta 199 se excluye por default (caso ya confirmado con Aunesa vía API).
Se toma como máximo `--por-cuenta` casos por cuenta para que la muestra sea
variada y no se agote en un solo cliente.

Uso:
    python -m scripts.diag_validacion_fantasmas
    python -m scripts.diag_validacion_fantasmas --n 12 --desde 2026-01-01
    python -m scripts.diag_validacion_fantasmas --incluir-199

No escribe nada (sólo SELECT). No pega a Aunesa.
"""
from __future__ import annotations

import argparse
from datetime import date

from psycopg.rows import dict_row

from core.postgres import get_pool

_CATS_VOLUMEN = (
    "compra", "venta",
    "suscripcion_fci", "solicitud_suscripcion_fci",
    "caucion_tom_ap", "caucion_col_ap",
)

_PESIF = ("CASE WHEN moneda = 'ARS' THEN abs(COALESCE(importe, 0)) "
          "ELSE abs(COALESCE(importe, 0)) * COALESCE(mep, 0) END")

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

_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def _ar(x) -> str:
    """Número en formato argentino (punto de miles, coma decimal)."""
    if x is None:
        return "—"
    return f"{float(x):,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def _ficha(n: int, sosp: dict, ctrl: dict | None, n_fant_dia: int) -> None:
    f = sosp["fecha"]
    print("\n" + "═" * 78)
    print(f"  CASO {n}")
    print("═" * 78)
    print(f"  CUENTA   {sosp['cuenta']}")
    print(f"  FECHA    {f}  ({_DIAS[f.weekday()]})")
    print(f"  Ese día, esa cuenta tiene {n_fant_dia} boleto(s) sospechoso(s) en total.")

    print("\n  ❓ SOSPECHOSO — buscá ESTE boleto en Aunesa:")
    print(f"       BOLETO      {sosp['comprobante']}")
    print(f"       OPERACIÓN   {sosp['categoria']}   ticker: {sosp['ticker'] or '—'}")
    print(f"       CANTIDAD    {_ar(sosp['cantidad'])}")
    print(f"       PRECIO      {_ar(sosp['precio'])}")
    print(f"       IMPORTE     {_ar(sosp['importe'])} {sosp['moneda']}")
    print(f"       TEXTO       {sosp['informacion']!r}")
    print("       ►► ESPERADO: NO tiene que estar. Si está, nuestro método falla.")

    if ctrl:
        print("\n  ✅ CONTROL — mismo día, misma cuenta. Buscá también este:")
        print(f"       BOLETO      {ctrl['comprobante']}")
        print(f"       OPERACIÓN   {ctrl['categoria']}   ticker: {ctrl['ticker'] or '—'}")
        print(f"       IMPORTE     {_ar(ctrl['importe'])} {ctrl['moneda']}")
        print(f"       TEXTO       {ctrl['informacion']!r}")
        print("       ►► ESPERADO: SÍ tiene que estar. Confirma que buscaste bien.")
    else:
        print("\n  ⚠️ Sin boleto de control para ese día/cuenta "
              "(TODOS los boletos de esa cuenta ese día son sospechosos).")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--desde", default=None, help="YYYY-MM-DD (default: 1 de enero del año)")
    ap.add_argument("--n", type=int, default=10, help="cuántos casos generar")
    ap.add_argument("--por-cuenta", type=int, default=2, help="máx. casos por cuenta")
    ap.add_argument("--incluir-199", action="store_true",
                    help="no excluir la cuenta 199 (caso ya confirmado)")
    args = ap.parse_args()

    desde = date.fromisoformat(args.desde) if args.desde else date(date.today().year, 1, 1)
    excluir = [] if args.incluir_199 else ["199"]

    print("\n" + "─" * 78)
    print("  VALIDACIÓN CRUZADA CONTRA AUNESA (sistema contable)")
    print("─" * 78)
    print("  Para cada caso vas a buscar DOS boletos en la app de Aunesa.")
    print("  Anotá para cada uno si APARECE o NO APARECE.")
    print("  Resultado que esperamos: control APARECE, sospechoso NO APARECE.")

    # Candidatos: fantasmas que SUMAN al volumen comercial, de mayor a menor plata.
    cand = _q(
        _CTE + f"""
        SELECT fecha, comprobante, id_cuenta, cuenta, categoria, ticker, cantidad,
               precio, importe, moneda, informacion, {_PESIF} AS pesif
          FROM marcadas
         WHERE fantasma AND categoria = ANY(%(cats)s)
           AND NOT (id_cuenta = ANY(%(excl)s))
         ORDER BY pesif DESC NULLS LAST
         LIMIT 400
        """,
        {"desde": desde, "cats": list(_CATS_VOLUMEN), "excl": excluir},
    )

    elegidos: list[dict] = []
    por_cuenta: dict[str, int] = {}
    for r in cand:
        idc = str(r["id_cuenta"])
        if por_cuenta.get(idc, 0) >= args.por_cuenta:
            continue
        por_cuenta[idc] = por_cuenta.get(idc, 0) + 1
        elegidos.append(r)
        if len(elegidos) >= args.n:
            break

    if not elegidos:
        print("\n  (no hay candidatos en la ventana elegida)")
        return

    for i, s in enumerate(elegidos, 1):
        ctx = _q(
            _CTE + f"""
            SELECT comprobante, categoria, ticker, importe, moneda, informacion,
                   fantasma, {_PESIF} AS pesif
              FROM marcadas
             WHERE fecha = %(f)s AND id_cuenta = %(idc)s
             ORDER BY pesif DESC NULLS LAST
            """,
            {"desde": desde, "f": s["fecha"], "idc": s["id_cuenta"]},
        )
        vivos = [r for r in ctx if not r["fantasma"]]
        n_fant = sum(1 for r in ctx if r["fantasma"])
        _ficha(i, s, vivos[0] if vivos else None, n_fant)

    # Checklist compacto para anotar mientras se consulta la app.
    print("\n\n" + "═" * 78)
    print("  CHECKLIST — completá con SÍ / NO")
    print("═" * 78)
    print(f"  {'#':<4}{'FECHA':<13}{'CUENTA':<8}{'BOLETO':<20}{'TIPO':<14}{'¿APARECE?'}")
    print("  " + "─" * 74)
    for i, s in enumerate(elegidos, 1):
        print(f"  {i:<4}{s['fecha']!s:<13}{s['id_cuenta']!s:<8}"
              f"{s['comprobante']:<20}{'SOSPECHOSO':<14}________")
    print("\n  (los CONTROL están en cada ficha de arriba)")
    print("\n✅ diag terminado (read-only).")


if __name__ == "__main__":
    main()
