"""diag_aca_benchmarks — ¿en qué unidad vienen las series macro que el histórico
de ACA podría automatizar? READ-ONLY.

POR QUÉ EXISTE (REGLA #2). La planilla de ACA compara la cartera contra Badlar,
Inflación y A3500. Las tres viven en `macro.series_macro`, así que la tentación
es cablearlas y listo. El problema es la UNIDAD: si `InflacionMensual` guarda
2.5 (por ciento) y el código la lee como 0.025 (fracción), el benchmark queda
100 veces mal — y en un gráfico acumulado eso no se ve como un error, se ve como
una serie. No puedo mirar la base de prod, así que en vez de suponer, esto lo
mide y el número decide.

QUÉ HACE LA VISTA MIENTRAS TANTO. `aca.series` nace con A3500 en
`macro_var:DOLAR` (variación mes contra mes: es un COCIENTE, no le importa la
unidad, no puede errarle por 100) y con Badlar e Inflación en `manual`. Con la
salida de este script se decide si conviene pasarlas a `macro_pct:<SERIE>` y con
qué `escala` (100 si vienen en porcentaje, 1 si vienen en fracción), desde
Manager → ACA. El valor manual SIEMPRE gana, así que activar el automático no
puede pisar nada de lo ya cargado.

Uso (Droplet, raíz):
    python -m scripts.diag_aca_benchmarks
    python -m scripts.diag_aca_benchmarks --meses 24
"""
from __future__ import annotations

import argparse

from api.services._sql import _f, _q

# Las series que el histórico de ACA querría automatizar, con la lectura que
# tendría sentido para cada una.
CANDIDATAS = [
    ("InflacionMensual", "pct", "Inflación mensual INDEC — ¿2.5 o 0.025?"),
    ("BADLAR",           "pct", "Badlar TNA — OJO: una TNA NO es el rendimiento "
                                "del mes; hay que mensualizarla"),
    ("DOLAR",            "var", "A3500 (nivel del TC) — se usa como variación"),
    ("TAMAR",            "pct", "TAMAR TNA — mismo caso que Badlar"),
    ("CER",              "var", "CER (índice) — se usa como variación"),
]


def _cierres(serie: str, meses: int) -> list[tuple[str, float | None]]:
    """Último valor de cada mes (el mismo criterio que usa aca._macro_mensual)."""
    rows = _q(
        "SELECT DISTINCT ON (to_char(fecha, 'YYYY-MM')) "
        "       to_char(fecha, 'YYYY-MM') AS periodo, valor, fecha "
        "FROM macro.series_macro WHERE serie = %(s)s AND valor IS NOT NULL "
        "ORDER BY to_char(fecha, 'YYYY-MM') DESC, fecha DESC "
        "LIMIT %(n)s",
        {"s": serie, "n": meses},
    )
    return [(r["periodo"], _f(r["valor"])) for r in reversed(rows)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--meses", type=int, default=12, help="cuántos meses mirar")
    args = ap.parse_args()

    print("=" * 78)
    print("ACA · histórico — unidades reales de las series macro candidatas")
    print("=" * 78)

    for serie, modo, nota in CANDIDATAS:
        cierres = _cierres(serie, args.meses)
        print(f"\n── {serie}  (uso propuesto: {modo})")
        print(f"   {nota}")
        if not cierres:
            print("   ⚠️  SIN DATOS en macro.series_macro — no se puede automatizar.")
            continue

        valores = [v for _, v in cierres if v is not None]
        print(f"   {len(cierres)} meses | min={min(valores):,.4f} | max={max(valores):,.4f}")
        for periodo, valor in cierres[-6:]:
            print(f"     {periodo}  {valor:>18,.6f}")

        if modo == "pct":
            # El veredicto que importa: leído como PORCENTAJE vs como FRACCIÓN,
            # ¿cuál de las dos da un rendimiento mensual creíble?
            ultimo = cierres[-1][1]
            if ultimo is not None:
                print(f"   → si `escala=100`: {ultimo / 100:.4%} mensual")
                print(f"   → si `escala=1`  : {ultimo:.4%} mensual")
                print("     Elegí la que se parezca al dato real del mes. Si el "
                      "número es una TNA (Badlar/TAMAR),")
                print("     NINGUNA de las dos sirve tal cual: hay que mensualizarla "
                      "antes → dejala MANUAL.")
        else:
            var = [(cierres[i][0], cierres[i][1] / cierres[i - 1][1] - 1)
                   for i in range(1, len(cierres))
                   if cierres[i][1] is not None and cierres[i - 1][1]]
            print("   → variación mensual que produciría `macro_var`:")
            for periodo, v in var[-6:]:
                print(f"     {periodo}  {v:>10.4%}")

    print("\n" + "=" * 78)
    print("Cómo aplicarlo: Manager → ACA → SERIES DEL HISTÓRICO. Poné la fuente en")
    print("`macro_pct:<SERIE>` (con su escala) o `macro_var:<SERIE>`. Lo cargado a")
    print("mano NUNCA se pisa: el automático solo rellena los meses vacíos.")
    print("=" * 78)


if __name__ == "__main__":
    main()
