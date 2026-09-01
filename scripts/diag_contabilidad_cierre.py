"""¿Por qué la CONTABILIDAD no ve tenencias que SÍ están en la tabla?

Read-only. Compara, para una cuenta y un mes, lo que hay en
`portafolio.tenencia` contra lo que efectivamente entra al informe, y dice en
qué paso se pierde cada fila. Tres sospechas, y las mide a las tres:

  1. LA FECHA. El informe pide el ÚLTIMO DÍA HÁBIL del mes. Si la tenencia de
     ese día no existe (o existe con otra fecha), el mes arranca vacío.
  2. EL FILTRO. `cartera IS DISTINCT FROM 'MONEDAS'`. Hasta 2026-09-01 era
     `cartera <> 'MONEDAS'`, y en SQL `NULL <> 'MONEDAS'` NO es TRUE: es NULL
     — toda fila SIN cartera se caía en silencio.
  3. EL CRUCE. La fila de tenencia y sus boletos se juntan por `unidad → key`
     (`portafolio.assets`). Si una unidad no está en el catálogo, su key es la
     unidad cruda y NO se junta con los boletos del mismo título: la tenencia
     queda en una fila y los boletos en otra, las dos a medias.

    python -m scripts.diag_contabilidad_cierre <id_cuenta> <YYYY-MM>
    python -m scripts.diag_contabilidad_cierre 100 2026-08
"""
from __future__ import annotations

import sys

from api.services._sql import _q
from core.calendario import ultimo_habil_del_mes


def _linea(t: str) -> None:
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}")


def _mes_anterior(anio: int, mes: int) -> tuple[int, int]:
    return (anio - 1, 12) if mes == 1 else (anio, mes - 1)


def _foto(id_cuenta: str, anio: int, mes: int, etiqueta: str) -> None:
    objetivo = ultimo_habil_del_mes(anio, mes)
    _linea(f"{etiqueta}  ·  cuenta {id_cuenta}  ·  {anio}-{mes:02d}"
           f"  ·  ultimo habil = {objetivo}")

    # 1. ¿QUÉ FECHAS tiene la tabla cerca del cierre? (scopeado por cuenta+mes)
    fechas = _q("SELECT fecha, count(*) AS filas FROM portafolio.tenencia "
                "WHERE id_cuenta = %(c)s AND fecha >= %(d)s AND fecha <= %(h)s "
                "GROUP BY fecha ORDER BY fecha DESC LIMIT 10",
                {"c": id_cuenta, "d": f"{anio}-{mes:02d}-01", "h": objetivo})
    print("\n-- ULTIMAS FECHAS CON TENENCIA EN EL MES --")
    if not fechas:
        print("  (NINGUNA fila en todo el mes para esta cuenta)")
    for r in fechas:
        marca = "  <-- el que pide el informe" if r["fecha"] == objetivo else ""
        print(f"  {r['fecha']}   {r['filas']:>6} filas{marca}")

    # 2. ¿QUÉ DESCARTA EL FILTRO en la fecha objetivo?
    print(f"\n-- COMPOSICION POR CARTERA EN {objetivo} --")
    comp = _q("SELECT coalesce(cartera, '(NULL)') AS cartera, count(*) AS filas, "
              "count(*) FILTER (WHERE cantidad IS NULL) AS sin_cantidad, "
              "count(*) FILTER (WHERE valuacion IS NULL) AS sin_valuacion "
              "FROM portafolio.tenencia WHERE id_cuenta = %(c)s AND fecha = %(f)s "
              "GROUP BY 1 ORDER BY 2 DESC", {"c": id_cuenta, "f": objetivo})
    total = sum(r["filas"] for r in comp)
    if not total:
        print("  (sin filas en esa fecha)")
        return
    for r in comp:
        nota = ""
        if r["cartera"] == "(NULL)":
            nota = "  <-- el `<>` VIEJO las tiraba en silencio"
        elif r["cartera"] == "MONEDAS":
            nota = "  <-- excluidas a proposito (cash, no es un titulo)"
        print(f"  {r['cartera']:<24} {r['filas']:>6} filas   "
              f"sin_cantidad={r['sin_cantidad']:<5} sin_valuacion={r['sin_valuacion']}"
              f"{nota}")
    entran = sum(r["filas"] for r in comp if r["cartera"] != "MONEDAS")
    print(f"\n  TOTAL en la fecha: {total}   ->  ENTRAN al informe: {entran}"
          f"   (se excluye solo MONEDAS)")


def _cruce(id_cuenta: str, anio: int, mes: int) -> None:
    """¿La tenencia y los boletos caen en la MISMA fila del informe?"""
    from api.services.pnl_sql import _mapas_assets
    u2m = _mapas_assets()["unidad_to_match"]
    objetivo = ultimo_habil_del_mes(anio, mes)
    _linea("CRUCE tenencia <-> boletos  (unidad -> key del informe)")

    ten = _q("SELECT DISTINCT unidad FROM portafolio.tenencia "
             "WHERE id_cuenta = %(c)s AND fecha = %(f)s "
             "AND cartera IS DISTINCT FROM 'MONEDAS'",
             {"c": id_cuenta, "f": objetivo})
    ops = _q("SELECT DISTINCT instrumento AS unidad FROM operaciones.operaciones "
             "WHERE id_cuenta = %(c)s AND anulado_en IS NULL "
             "AND to_char(concertacion, 'YYYY-MM') = %(m)s "
             "AND instrumento IS NOT NULL",
             {"c": id_cuenta, "m": f"{anio}-{mes:02d}"})

    sin_catalogo = [r["unidad"] for r in ten if r["unidad"] not in u2m]
    print(f"\n  unidades en tenencia {objetivo}: {len(ten)}")
    print(f"  unidades en boletos del mes:     {len(ops)}")
    print(f"  unidades de tenencia SIN entrada en portafolio.assets: {len(sin_catalogo)}")
    if sin_catalogo:
        print("    (su key del informe es la UNIDAD CRUDA -> no se junta con "
              "los boletos si el boleto sí mapea)")
        for u in sin_catalogo[:15]:
            print(f"      {u}")

    keys_ten = {u2m.get(r["unidad"], r["unidad"]) for r in ten}
    keys_ops = {u2m.get(r["unidad"], r["unidad"]) for r in ops}
    solo_ops = sorted(keys_ops - keys_ten)
    print(f"\n  keys con BOLETOS pero SIN tenencia al cierre: {len(solo_ops)}")
    print("    (son las filas que salen con nominales en 0 y el ⚠ del cuadre)")
    for k in solo_ops[:20]:
        print(f"      {k}")


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(2)
    id_cuenta, mes_str = sys.argv[1], sys.argv[2]
    anio, mes = int(mes_str[:4]), int(mes_str[5:7])
    a0, m0 = _mes_anterior(anio, mes)
    _foto(id_cuenta, a0, m0, "CIERRE INICIAL (el mes ANTERIOR)")
    _foto(id_cuenta, anio, mes, "CIERRE FINAL (el mes del informe)")
    _cruce(id_cuenta, anio, mes)
    print("\nListo. Read-only: no escribió nada.\n")


if __name__ == "__main__":
    main()
