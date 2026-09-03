"""READ-ONLY. El ≠ del SALDO AL CIERRE: los dos números que informa el banco.

Herramienta: diag · Cero escrituras. Corre sobre las fechas que hay en la base.

EL PUNTO
========

En el CONSOLIDADO, una cuenta puede mostrar el badge **≠** con el mensaje «el
extracto cierra en X y el saldo informado dice Y». Los dos los manda Interbanking,
por **dos APIs distintas**, y la pantalla se queda con el del EXTRACTO:

  · `bancos.extracto_dia.saldo_cierre` ← API de **Extractos** (`ending_balance`).
    Viene con apertura, créditos y débitos, así que se puede AUDITAR:
    `apertura + créditos − débitos = cierre`. Solo existe los días CON movimientos.
  · `bancos.saldos`                    ← API de **Saldos**. Responde se haya
    movido la cuenta o no, y por eso existe: con la ventana corta del back office,
    una cuenta quieta no tiene ninguna fila de extracto.

La precedencia («extracto si lo hay, si no el informado») vive en UN solo lugar,
`bancos._saldos_banco()`, y es la misma para TODAS las cuentas. Cuando los dos
números coinciden —el caso normal— no se nota cuál ganó; el ≠ aparece solo cuando
difieren.

LO QUE ESTE DIAG VIENE A MEDIR
=============================

Que difieran puede ser dos cosas MUY distintas, y no se puede decidir sin ver los
números:

  (a) **No son el mismo concepto.** Hasta el 2026-09-01 el lado «informado» se
      armaba con `coalesce(saldo_operativo, saldo_dia)` y el operativo —la foto de
      HOY, lo disponible ahora— le ganaba al saldo del día. Hoy manda `saldo_dia`;
      el diag igual imprime los dos, porque descartar esta causa vale tanto como
      confirmarla.
  (b) **El extracto no cierra.** Si `apertura + créditos − débitos ≠ cierre`, el
      sospechoso es la aritmética del extracto.
  (c) **Una CABECERA no coincide con su propio DETALLE.** Los dos números de
      arriba son cabeceras: totales que el banco declara. El detalle —los
      movimientos, uno por uno— es la **tercera fuente, y la única independiente
      de las dos**. Si la cabecera del extracto dice créditos por X y sus propios
      movimientos suman Y, la cabecera está describiendo otra cosa (otro período,
      otro extracto, un acumulado) y ahí no hay nada que comparar: hay que
      arreglar la ingesta.
  (d) **EL SALDO SALTA Y NO HAY MOVIMIENTOS QUE LO EXPLIQUEN.** Es la pregunta que
      trajo el back office el 2026-09-03 («el cierre da −127.566,23 y el día no
      tiene un solo movimiento»). Se contesta al final, en su propio bloque, y con
      el sospechoso nombrado: un día QUIETO no tiene fila en `historical_balances`,
      así que su único saldo es el de la FOTO — y una foto intradiaria no es un
      cierre.

Por eso imprime **tres bloques por fila**, no dos: cabecera del extracto,
cabecera de saldos, y el detalle guardado con su propia aritmética. Y dice quién
no coincide con quién, que es la pregunta que la pantalla no puede contestar.

⚠️ **El «informado» lo resuelve el ÁRBITRO, no una copia.** Este diag llegó a
tener escrita a mano la precedencia vieja (`operativo` antes que `saldo_dia`), o
sea que seguía anunciando «lo que compara la pantalla» **cuatro días después de
que la pantalla dejara de compararlo así**. Un diag que miente es peor que no
tenerlo: se lo usa justo cuando nadie más sabe qué está pasando. Ahora el valor
sale de `bancos._SALDO_INFORMADO` (en la query) y la precedencia de
`bancos._cierre_del_banco()` — los MISMOS que dibujan la pantalla. REGLA #9.

Uso:
    python -m scripts.diag_saldo_cierre
    python -m scripts.diag_saldo_cierre --solo-diferencias
    python -m scripts.diag_saldo_cierre --cuenta 12            # por id
    python -m scripts.diag_saldo_cierre --cuenta 2820352686    # o por nº de cuenta
"""
from __future__ import annotations

import argparse
import json
from itertools import pairwise

from api.services import bancos as B


def _n(v) -> float | None:
    return None if v is None else float(v)


def _p(v) -> str:
    return "—" if v is None else f"{v:>18,.2f}"


def _filas(cuenta: str | None) -> list[dict]:
    """Una fila por (cuenta, fecha) que exista en CUALQUIERA de las dos tablas.

    El universo de fechas es la UNIÓN y no una de las dos: si arrancara del
    extracto, las cuentas quietas —que son justo las que esta API vino a
    cubrir— no aparecerían nunca; si arrancara de saldos, se perderían los días
    que el banco informa por extracto y no por saldos.

    `cuenta` acepta el **id** o el **número de cuenta**, que es lo único que se
    ve en pantalla. Pedirle al que concilia que primero averigüe un id interno
    es lo que hace que el diag no se use.
    """
    where, args = "c.activa", ()
    if cuenta is not None:
        where = "(c.account_number = %s OR c.id::text = %s)"
        args = (cuenta, cuenta)
    return B._q(
        f"""WITH dias AS (
                SELECT cuenta_id, fecha FROM bancos.extracto_dia
                UNION
                SELECT cuenta_id, fecha FROM bancos.saldos
            )
            SELECT c.id, c.bank_name, c.account_number, c.currency, c.origen,
                   d.fecha,
                   e.saldo_apertura, e.saldo_cierre, e.total_creditos,
                   e.total_debitos, e.total_movimientos, e.numero_extracto,
                   e.cierra, e.diferencia,
                   s.saldo_dia, s.creditos_dia, s.debitos_dia, s.saldo_contable,
                   s.saldo_operativo, s.saldo_operativo_ini, s.proyectado_24hs,
                   s.proyectado_48hs, s.es_foto, s.sincronizado_at, s.raw,
                   -- El «informado» sale del ÁRBITRO, no de una copia escrita acá:
                   -- es la MISMA expresión que usan el consolidado, el cierre y
                   -- DIFERENCIAS. Si acá se resolviera distinto, el diag mentiría
                   -- justo el día que se lo consulta. REGLA #9.
                   {B._SALDO_INFORMADO} AS informado,
                   p.fuente AS elegida,
                   -- LA TERCERA FUENTE: el detalle que de verdad guardamos. Es
                   -- lo único independiente de las dos cabeceras — si el header
                   -- del extracto no coincide con sus propios movimientos, el
                   -- sospechoso deja de ser un misterio.
                   d2.n_movs, d2.cr_movs, d2.db_movs, d2.extractos,
                   -- Lo que se SELLÓ para ese día: es lo que mañana se lee como
                   -- SALDO INICIO, así que un cierre malo no se queda quieto.
                   cd.saldo AS sellado, cd.fuente AS sellado_fuente,
                   -- Los manuales del día: nuestro saldo los suma, el del banco no.
                   mm.ajuste AS ajuste_manual
              FROM dias d
              JOIN bancos.cuentas   c ON c.id = d.cuenta_id
              LEFT JOIN bancos.extracto_dia e
                     ON e.cuenta_id = d.cuenta_id AND e.fecha = d.fecha
              LEFT JOIN bancos.saldos       s
                     ON s.cuenta_id = d.cuenta_id AND s.fecha = d.fecha
              LEFT JOIN bancos.fuente_elegida p
                     ON p.cuenta_id = d.cuenta_id AND p.fecha = d.fecha
              LEFT JOIN bancos.cierres_diarios cd
                     ON cd.cuenta_id = d.cuenta_id AND cd.fecha = d.fecha
              LEFT JOIN (SELECT cuenta_id, fecha,
                                sum(CASE WHEN tipo = 'C' THEN abs(importe)
                                         ELSE -abs(importe) END) AS ajuste
                           FROM bancos.movimientos_manuales
                          GROUP BY cuenta_id, fecha) mm
                     ON mm.cuenta_id = d.cuenta_id AND mm.fecha = d.fecha
              LEFT JOIN (SELECT cuenta_id, fecha, count(*) AS n_movs,
                                sum(CASE WHEN tipo = 'C' THEN abs(importe) ELSE 0 END)
                                  AS cr_movs,
                                sum(CASE WHEN tipo = 'D' THEN abs(importe) ELSE 0 END)
                                  AS db_movs,
                                count(DISTINCT numero_extracto) AS extractos
                           FROM bancos.movimientos
                          GROUP BY cuenta_id, fecha) d2
                     ON d2.cuenta_id = d.cuenta_id AND d2.fecha = d.fecha
             WHERE {where}
             ORDER BY c.bank_name, c.account_number, d.fecha""",
        args)


def _cierre_efectivo(r: dict) -> tuple[float | None, str | None]:
    """El cierre del banco de esa fila, con el MISMO árbitro que la pantalla.

    No se recalcula la precedencia acá: se llama a `bancos._cierre_del_banco()`,
    que es el único lugar donde está escrita. Un diag con su propia copia de la
    regla puede decir que todo está bien mientras la pantalla muestra otra cosa.
    """
    return B._cierre_del_banco(_n(r["informado"]), _n(r["saldo_cierre"]),
                               r.get("elegida"))


def _saltos(filas: list[dict]) -> None:
    """**Días donde el saldo salta y los movimientos no lo explican.**

    La aritmética es la de la pantalla DIFERENCIAS:

        sin_explicar  =  (cierre − cierre_previo)  −  Σ movimientos del día

    y se calcula con el árbitro, no con una fuente elegida a mano. Lo que agrega
    este bloque es **el sospechoso**: cuando el día no tiene `saldo_dia` y el
    cierre está saliendo del `saldo_operativo`, el salto no es del banco — es
    nuestro, porque estamos leyendo una FOTO INTRADIARIA como si fuera un cierre.
    """
    print("\n" + "=" * 100)
    print("SALTOS SIN MOVIMIENTOS QUE LOS EXPLIQUEN")
    print("=" * 100)
    por_cuenta: dict[int, list[dict]] = {}
    for r in filas:
        por_cuenta.setdefault(r["id"], []).append(r)

    hallados = 0
    for rs in por_cuenta.values():
        rs = sorted(rs, key=lambda x: x["fecha"])
        for previa, hoy in pairwise(rs):
            ayer_v, ayer_f = _cierre_efectivo(previa)
            hoy_v, hoy_f = _cierre_efectivo(hoy)
            if ayer_v is None or hoy_v is None:
                continue
            neto = round((_n(hoy["cr_movs"]) or 0.0) - (_n(hoy["db_movs"]) or 0.0), 2)
            salto = round(hoy_v - ayer_v, 2)
            sin_explicar = round(salto - neto, 2)
            if abs(sin_explicar) < 0.01:
                continue
            hallados += 1
            print(f"\n[{hoy['id']}] {hoy['bank_name']} {hoy['account_number']} "
                  f"{hoy['currency']}   {previa['fecha']} → {hoy['fecha']}")
            print(f"    cierre {previa['fecha']}  {_p(ayer_v)}  (fuente: {ayer_f})")
            print(f"    cierre {hoy['fecha']}  {_p(hoy_v)}  (fuente: {hoy_f})")
            print(f"    salto                 {_p(salto)}")
            print(f"    Σ movimientos del día {_p(neto)}   ({hoy['n_movs'] or 0} movimientos)")
            print(f"    SIN EXPLICAR          {_p(sin_explicar)}")
            if hoy["ajuste_manual"] is not None:
                print(f"    (ajuste manual del día: {_p(_n(hoy['ajuste_manual']))} — "
                      "nuestro saldo lo suma, el del banco no)")

            # EL SOSPECHOSO. Acá está la diferencia entre «el banco se
            # contradice» y «le estamos preguntando otra cosa al banco».
            if hoy_f == "saldo" and _n(hoy["saldo_dia"]) is None:
                print("    ⚠️  SOSPECHOSO: ese día NO tiene `saldo_dia` — el banco no lo")
                print("        informó en `historical_balances` (día quieto). El cierre")
                print("        está saliendo del SALDO OPERATIVO, que es una FOTO")
                print(f"        INTRADIARIA (es_foto={hoy['es_foto']}, "
                      f"sincronizado {hoy['sincronizado_at']}), no un cierre contable.")
                print(f"        saldo_contable de ese día: {_p(_n(hoy['saldo_contable']))}"
                      "   ← ESTE sí es el saldo contable")
                cont = _n(hoy["saldo_contable"])
                if cont is not None and abs(round(cont - ayer_v - neto, 2)) < 0.01:
                    print("        ✓ y con el CONTABLE el día cierra exacto contra el "
                          "cierre anterior.")
            elif _n(hoy["saldo_dia"]) is not None:
                print("    → el salto viene del `saldo_dia` que informó el banco: acá el")
                print("      sospechoso NO es la fuente, es el dato o un movimiento que")
                print("      el banco imputó a otra fecha.")
            if hoy["sellado"] is not None:
                print(f"    sellado en cierres_diarios: {_p(_n(hoy['sellado']))} "
                      f"(fuente {hoy['sellado_fuente']}) ← esto viaja al SALDO INICIO "
                      "del día siguiente")

    if not hallados:
        print("\n  ✓ Ningún salto sin explicar en las fechas que hay en la base.")
    print(f"\n  saltos sin explicar: {hallados}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cuenta", default=None,
                    help="acotar a UNA cuenta: id interno o nº de cuenta")
    ap.add_argument("--solo-diferencias", action="store_true",
                    help="solo las filas donde los dos números no coinciden")
    ap.add_argument("--raw", action="store_true",
                    help="además, el bloque `balances` crudo que manda el banco")
    args = ap.parse_args()

    filas = _filas(args.cuenta)
    con_dif = 0
    solo_definicion = 0
    ext_no_cierra = 0
    mostradas = 0

    print("=" * 100)
    print("SALDO AL CIERRE — extracto vs. saldo informado")
    print("=" * 100)

    for r in filas:
        fecha = r["fecha"]
        cierre = _n(r["saldo_cierre"])
        dia = _n(r["saldo_dia"])
        operativo = _n(r["saldo_operativo"])
        # Exactamente lo que compara el consolidado para dibujar el ≠, resuelto
        # por el ÁRBITRO (`_SALDO_INFORMADO`, en la query). Acá había una copia a
        # mano de la precedencia VIEJA —`operativo` antes que `saldo_dia`—, o sea
        # que el diag anunciaba «lo que compara la pantalla» cuatro días después
        # de que la pantalla dejara de compararlo así.
        informado = _n(r["informado"])
        dif = (None if cierre is None or informado is None
               else round(cierre - informado, 2))
        # La misma comparación pero contra el SALDO DEL DÍA, que es el número
        # homogéneo con un cierre. Si acá da 0 y arriba no, la diferencia era
        # de definición y no un error.
        dif_dia = None if cierre is None or dia is None else round(cierre - dia, 2)

        hay_dif = dif is not None and abs(dif) >= 0.01
        if hay_dif:
            con_dif += 1
            if dif_dia is not None and abs(dif_dia) < 0.01:
                solo_definicion += 1
        if r["cierra"] is False:
            ext_no_cierra += 1
        if args.solo_diferencias and not hay_dif:
            continue
        mostradas += 1

        print(f"\n[{r['id']}] {r['bank_name']} {r['account_number']} {r['currency']}"
              f"   {fecha}   origen={r['origen'] or 'interbanking'}"
              + ("   ← ≠ EN PANTALLA" if hay_dif else ""))

        print("  ① EXTRACTO — la CABECERA (API Extractos; solo días CON movimientos)")
        print(f"    apertura            {_p(_n(r['saldo_apertura']))}")
        print(f"    + créditos          {_p(_n(r['total_creditos']))}")
        print(f"    − débitos           {_p(_n(r['total_debitos']))}")
        print(f"    = CIERRE            {_p(cierre)}")
        print(f"    movs que DECLARA    {r['total_movimientos']}"
              f"      extracto Nº {r['numero_extracto']}")
        if r["cierra"] is None:
            print("    verificación: el banco no mandó todo lo necesario para chequearla")
        elif r["cierra"]:
            print("    verificación: ✓ apertura + créditos − débitos = cierre")
        else:
            print(f"    verificación: ✗ NO CIERRA por {_n(r['diferencia']):,.2f} "
                  "← acá el sospechoso ES el extracto")

        print("  ② SALDOS — la otra CABECERA (API Saldos; responde se haya movido o no)")
        print(f"    saldo_dia           {_p(dia)}   ← el saldo DEL DÍA (historical_balances)")
        print(f"    créditos del día    {_p(_n(r['creditos_dia']))}")
        print(f"    débitos del día     {_p(_n(r['debitos_dia']))}")
        print(f"    saldo_contable      {_p(_n(r['saldo_contable']))}")
        print(f"    saldo_operativo     {_p(operativo)}   ← foto de HOY, NO el cierre de un día")
        print(f"    saldo_operativo_ini {_p(_n(r['saldo_operativo_ini']))}")
        print(f"    proyectado 24 / 48  {_p(_n(r['proyectado_24hs']))} / "
              f"{_p(_n(r['proyectado_48hs']))}")
        print(f"    es_foto             {r['es_foto']}"
              f"      sincronizado {r['sincronizado_at']}")

        # ③ El DETALLE. Es la única fuente independiente de las dos cabeceras:
        #    los movimientos que el banco mandó uno por uno y nosotros guardamos.
        n_movs = r["n_movs"] or 0
        cr_movs, db_movs = _n(r["cr_movs"]) or 0.0, _n(r["db_movs"]) or 0.0
        neto_movs = round(cr_movs - db_movs, 2)
        apertura = _n(r["saldo_apertura"])
        print(f"  ③ DETALLE — los movimientos GUARDADOS ({n_movs}, "
              f"{r['extractos'] or 0} extracto/s distintos)")
        print(f"    Σ créditos          {_p(round(cr_movs, 2))}")
        print(f"    Σ débitos           {_p(round(db_movs, 2))}")
        print(f"    neto                {_p(neto_movs)}")
        if apertura is not None:
            print(f"    apertura + neto     {_p(round(apertura + neto_movs, 2))}"
                  "   ← el cierre que sale del detalle")

        print("  ¿QUIÉN NO COINCIDE CON QUIÉN?")
        cabecera_vs_detalle = (
            None if r["total_creditos"] is None
            else round((_n(r["total_creditos"]) or 0.0) - cr_movs, 2))
        if r["total_movimientos"] is not None and r["total_movimientos"] != n_movs:
            print(f"    ⚠️ el extracto DECLARA {r['total_movimientos']} movimientos y hay "
                  f"{n_movs} guardados — faltan páginas o el banco declara otra cosa")
        if cabecera_vs_detalle is not None and abs(cabecera_vs_detalle) >= 0.01:
            print(f"    ⚠️ los CRÉDITOS de la cabecera no dan la suma de sus propios "
                  f"movimientos: {_p(cabecera_vs_detalle).strip()} de más")
            print("       → el sospechoso es LA CABECERA DEL EXTRACTO, no el saldo informado")
        elif cabecera_vs_detalle is not None:
            print("    ✓ la cabecera del extracto coincide con sus propios movimientos")
        if r["creditos_dia"] is not None:
            d_cr = round((_n(r["creditos_dia"]) or 0.0) - cr_movs, 2)
            print(f"    créditos de la API de Saldos vs. el detalle: "
                  f"{_p(d_cr).strip()} de diferencia")

        print("  COMPARACIÓN DE SALDOS")
        cual = "saldo_dia" if dia is not None else "operativo"
        print(f"    lo que compara la pantalla   cierre − {cual}"
              f"  = {_p(dif)}")
        print(f"    contra el SALDO DEL DÍA      cierre − saldo_dia   = {_p(dif_dia)}")
        if hay_dif and dif_dia is not None and abs(dif_dia) < 0.01:
            print("    ⚠️  Contra el saldo del día da CERO: el ≠ sale de comparar el cierre")
            print("        contra el saldo OPERATIVO, que es otra cosa. Diferencia de")
            print("        DEFINICIÓN, no un error del banco.")

        if args.raw and r.get("raw"):
            crudo = r["raw"] if isinstance(r["raw"], dict) else json.loads(r["raw"])
            print("  RAW balances: " + json.dumps(crudo.get("balances") or {},
                                                  ensure_ascii=False))

    print("\n" + "=" * 100)
    print(f"filas miradas: {len(filas)}   ·   mostradas: {mostradas}")
    print(f"con ≠ en pantalla:                         {con_dif}")
    print(f"  de esas, que dan CERO contra saldo_dia:  {solo_definicion}"
          "   ← diferencia de definición, no error")
    print(f"extractos que NO cierran (aritmética):     {ext_no_cierra}")
    print("=" * 100)

    # El bloque (d): el salto que ninguna de las comparaciones de arriba ve,
    # porque no está ADENTRO de un día sino ENTRE dos.
    _saltos(filas)


if __name__ == "__main__":
    main()
