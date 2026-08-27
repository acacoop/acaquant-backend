"""`scripts/diag_ficha_incompleta.py` — CUÁNTA FICHA FALTA, y de qué forma.

Read-only. Es la MEDICIÓN previa a darle al agente la habilidad `ficha_incompleta`
(pedido del user 2026-08-27: expandir el control `assets_sin_cartera` para que
también mire `clase_activo` y `emisor`, y sumar `fci_incompletos`).

⚠️ **Por qué se mide antes y no después.** El control viejo escanea los ~2.000
assets sin filtrar por nada, y sumarle dos campos más podría hacer que la primera
corrida abra cientos de hallazgos de golpe. Un tablero que nace con 400 filas no
se lee nunca. Antes de elegir el alcance hay que ver el número: eso es lo que
contesta este script, y por eso mide las TRES ventanas por separado.

    LAS TRES VENTANAS, de la más ancha a la que duele
    =================================================

    TODO         los ~2.000 assets del catálogo. Es lo que mira el control hoy.
    VIGENTE      los que todavía existen en el mercado (`assets.vigente`). Un
                 papel que amortizó no se puede borrar —la tenencia histórica lo
                 referencia— pero tampoco tiene sentido pedirle la ficha.
    CON TENENCIA los que la casa TIENE HOY. **Estos son los únicos que rompen
                 una pantalla**: el resto es catálogo. Es el mismo criterio que
                 el control `titulos_sin_flujo` ya aplica y que el detector del
                 agente todavía no («un bono sin flujo que no tenemos no cuesta
                 nada hoy; uno que tenemos NO VALÚA»).

    Y LA PREGUNTA QUE DECIDE LA FORMA DE LA HABILIDAD
    =================================================

    **¿Son N problemas o son tres grupos?** 379 títulos sin clase no son 379
    problemas si caen en tres carteras: ahí es UNA decisión de la mesa aplicada
    a un grupo. Si están desparramados en veinte, es una lista y hay que
    atacarla de otra forma. Un hallazgo por fila sobre 379 filas no lo lee
    nadie — y agrupar sin mirar cómo se reparten sería inventar el criterio.

    QUÉ ROMPE CADA CAMPO (verificado en el código, no supuesto)
    ==========================================================

    cartera       el divisor de la valuación lo decide la CARTERA (÷100 o no).
                  Sin ella la fila queda SIN CLASIFICAR en el AuM, y en `/aca`
                  la plata cae al balde «otras» (`aca.py`: «sin ficha → sin
                  cartera»).
    clase_activo  `/aca` abre por MONEDA con `_moneda_de(cartera, clase)`, y la
                  regla de CLASE gana sobre la de cartera. Sin clase, si la
                  cartera tampoco resuelve, la plata va a «sin_clasificar».
    emisor        cualquier cosa que AGRUPE por emisor cuenta mal, y no se nota:
                  las filas existen y suman bien por separado.
    ticker (FCI)  el fondo sale SIN NOMBRE en el detalle de /aum → FCI y, si
                  varios comparten el vacío, **se fusionan en un renglón mudo**.

    Y LA COLUMNA QUE DECIDE SI HAY BOTÓN
    ====================================

    `¿DERIVABLE?` cuenta a cuántos les puede completar el campo una regla de
    `jobs/assets_autofill` (que ya corre todas las noches y ya sabe derivar
    `cartera`, `clase_activo`, `ticker` y heredar `emisor`). Esos son los que
    pueden tener ARREGLO; el resto es aviso y se carga a mano en Manager.
    Sin este número no se puede declarar la habilidad sin mentir: el invariante
    7 exige declarar el arreglo O declarar que no hay.

Uso:
    python -m scripts.diag_ficha_incompleta
    python -m scripts.diag_ficha_incompleta --detalle   # las filas, no el conteo
"""
from __future__ import annotations

import argparse

from core.postgres import get_pool

# Los mismos predicados de vacío que usa el control viejo, para que el número
# sea comparable con lo que hoy canta `assets_sin_cartera` / `fci_incompletos`.
#
# ⚠️ «NO APLICA» cuenta como vacío igual que el NULL: es lo que el control ya
# hacía, y tiene razón — una cartera «NO APLICA» no le da divisor a nadie.
#
# ⚠️ **TODAS LAS COLUMNAS VAN CALIFICADAS CON `a.`** (bug del 2026-08-27).
# `cartera` y `ticker` existen en `portafolio.assets` **y** en
# `portafolio.tenencia`, así que en la query de PLATA —que joinea las dos— un
# `cartera` pelado es ambiguo y Postgres corta. Y lo peor sería que NO cortara:
# resolvería contra la tenencia, y estaríamos midiendo la cartera del snapshot
# en vez de la de la ficha, que es justo la que falta.
_CARTERA_VACIA = ("(a.cartera IS NULL OR btrim(a.cartera) = '' "
                  "OR upper(btrim(a.cartera)) = 'NO APLICA')")

CAMPOS: list[tuple[str, str, str]] = [
    # (regla, condición SQL de «falta», qué rompe)
    ("sin_cartera", _CARTERA_VACIA,
     "la valuación queda SIN CLASIFICAR (la cartera decide el divisor)"),
    ("sin_clase_activo", "(a.clase_activo IS NULL OR btrim(a.clase_activo) = '')",
     "/aca no lo puede abrir por moneda: cae en «sin_clasificar»"),
    ("sin_emisor", "(a.emisor IS NULL OR btrim(a.emisor) = '')",
     "agrupar por emisor cuenta mal, y las filas suman bien por separado"),
    ("fci_sin_ticker",
     "(upper(btrim(coalesce(a.cartera,''))) IN ('FCI','CARTERA FCI') "
     " AND (a.ticker IS NULL OR btrim(a.ticker) = ''))",
     "el fondo sale SIN NOMBRE en /aum → FCI y se fusiona con otros vacíos"),
]


def _q(sql: str, params: tuple = ()) -> list[tuple]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _titulo(s: str) -> None:
    print(f"\n{'═' * 78}\n {s}\n{'═' * 78}")


# La foto de tenencia más reciente. UNA fecha, por índice — nunca un scan de la
# tenencia histórica (REGLA #4).
_ULTIMA_FECHA = "(SELECT max(fecha) FROM portafolio.tenencia)"
_CON_TENENCIA = (
    f"EXISTS (SELECT 1 FROM portafolio.tenencia t "
    f"         WHERE t.unidad = a.unidad AND t.fecha = {_ULTIMA_FECHA})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--detalle", action="store_true",
                    help="listar las filas en vez de contarlas")
    a = ap.parse_args()

    # ── EL UNIVERSO ────────────────────────────────────────────────────────
    _titulo("EL UNIVERSO — sobre qué se está contando")
    tot, vig, con_ten = _q(
        f"SELECT count(*), "
        f"       count(*) FILTER (WHERE coalesce(a.vigente, true)), "
        f"       count(*) FILTER (WHERE {_CON_TENENCIA}) "
        f"  FROM portafolio.assets a")[0]
    fecha = _q(f"SELECT {_ULTIMA_FECHA}")[0][0]
    print(f"  assets en el catálogo ............ {tot:>6,}")
    print(f"  vigentes ......................... {vig:>6,}")
    print(f"  CON TENENCIA al {str(fecha)[:10]} ..... {con_ten:>6,}"
          f"   ← los únicos que rompen una pantalla hoy")

    # ── LO QUE FALTA, POR VENTANA ──────────────────────────────────────────
    _titulo("QUÉ FALTA — el mismo campo, en las tres ventanas")
    print(f"  {'REGLA':18} {'TODO':>7} {'VIGENTE':>9} {'C/TENENCIA':>11}")
    for regla, cond, _rompe in CAMPOS:
        n_tot, n_vig, n_ten = _q(
            f"SELECT count(*), "
            f"       count(*) FILTER (WHERE coalesce(a.vigente, true)), "
            f"       count(*) FILTER (WHERE {_CON_TENENCIA}) "
            f"  FROM portafolio.assets a WHERE {cond}")[0]
        print(f"  {regla:18} {n_tot:>7,} {n_vig:>9,} {n_ten:>11,}")

    print("\n  QUÉ ROMPE CADA UNO:")
    for regla, _cond, rompe in CAMPOS:
        print(f"    {regla:18} → {rompe}")

    # ── ¿SON N PROBLEMAS O SON TRES GRUPOS? ────────────────────────────────
    #
    # **Esta es la pregunta que decide la forma de la habilidad.** 379 títulos
    # sin clase no son 379 problemas si son tres carteras: ahí es UNA decisión
    # de la mesa aplicada a un grupo. Si en cambio están desparramados en veinte
    # carteras, entonces sí es una lista y hay que atacarla por otro lado.
    #
    # Un hallazgo por fila sobre 379 filas no lo lee nadie, y agrupar sin mirar
    # cómo se reparten sería inventar el criterio.
    _titulo("CÓMO SE REPARTEN — solo los que TIENEN TENENCIA")
    for regla, cond, _r in CAMPOS:
        filas = _q(
            f"SELECT coalesce(nullif(btrim(a.cartera), ''), '(sin cartera)'), "
            f"       count(*) "
            f"  FROM portafolio.assets a "
            f" WHERE {cond} AND {_CON_TENENCIA} "
            f" GROUP BY 1 ORDER BY 2 DESC LIMIT 12")
        n = sum(c for _, c in filas)
        if not n:
            continue
        print(f"\n  {regla}  ({n} con tenencia, top {len(filas)} carteras)")
        for cartera, c in filas:
            print(f"      {cartera[:34]:34} {c:>5}")

    # ── ¿CUÁNTOS TENDRÍAN BOTÓN? ───────────────────────────────────────────
    #
    # Se le pregunta a las reglas REALES de `assets_autofill`, no a una copia:
    # si acá hubiera una segunda opinión sobre qué se puede derivar, el botón
    # prometería cosas que el arreglo no puede cumplir (REGLA #9).
    _titulo("¿CUÁNTOS TIENEN ARREGLO? — se le pregunta a assets_autofill")
    try:
        from jobs import assets_autofill as af

        filas = af.leer_catalogo()
        # Las dos pasadas que necesitan contexto de TODO el catálogo. Si alguna
        # no está disponible, se dice: un conteo incompleto que se lee como
        # completo es peor que no tenerlo.
        for paso, fn in (("especies", lambda: af.anotar_especies(
                              filas, af.leer_especies())),
                         ("herencia", lambda: af.anotar_herencia(filas))):
            try:
                fn()
                print(f"  · pasada «{paso}»: corrió")
            except Exception as e:
                print(f"  ⚠ la pasada «{paso}» NO corrió ({type(e).__name__}: "
                      f"{e}) — el conteo de derivables queda POR DEBAJO del real")
        # ⚠️ **CERO DERIVABLES SE LEE DE DOS FORMAS Y NO SON LA MISMA**: «no hay
        # nada que ninguna regla pueda completar» (el job nocturno ya hizo todo
        # lo suyo y lo que queda es carga manual) o «la pasada no corrió y no
        # medí nada». Por eso las dos líneas de arriba se imprimen siempre.
        print(f"  · catálogo leído: {len(filas):,} filas\n")

        derivables: dict[str, int] = {}
        for f in filas:
            propuesto: dict[str, str] = {}
            for r in af.REGLAS:
                try:
                    propuesto.update(r.fn(f) or {})
                except Exception:
                    continue
            for campo, clave in (("cartera", "sin_cartera"),
                                 ("clase_activo", "sin_clase_activo"),
                                 ("emisor", "sin_emisor"),
                                 ("ticker", "fci_sin_ticker")):
                vacio = not str(f.get(campo) or "").strip()
                if vacio and str(propuesto.get(campo) or "").strip():
                    derivables[clave] = derivables.get(clave, 0) + 1

        print(f"  {'REGLA':18} {'DERIVABLES':>11}   (los demás son AVISO: "
              f"se cargan a mano en Manager → ASSETS)")
        for regla, _c, _r in CAMPOS:
            print(f"  {regla:18} {derivables.get(regla, 0):>11,}")
        print("\n  → los DERIVABLES son los que pueden tener botón «completar "
              "ficha»;\n    el resto entra como aviso y no promete nada.")
    except Exception as e:
        print(f"  ✗ no pude preguntarle a assets_autofill: "
              f"{type(e).__name__}: {e}")
        print("    (sin este número no se puede declarar el arreglo sin mentir)")

    # ── EL DETALLE ─────────────────────────────────────────────────────────
    if a.detalle:
        for regla, cond, _r in CAMPOS:
            _titulo(f"DETALLE — {regla} (solo CON TENENCIA)")
            filas = _q(
                f"SELECT a.unidad, a.cartera, a.clase_activo, a.emisor, a.ticker "
                f"  FROM portafolio.assets a "
                f" WHERE {cond} AND {_CON_TENENCIA} ORDER BY a.unidad LIMIT 60")
            if not filas:
                print("  — ninguno")
            for u, ca, cl, em, tk in filas:
                print(f"  {u[:58]:58} cartera={ca or '—'} clase={cl or '—'} "
                      f"emisor={(em or '—')[:20]} ticker={tk or '—'}")

    print("\n  Nada de esto escribe. Es solo para elegir el alcance de la "
          "habilidad\n  con el número a la vista, y no a ojo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
