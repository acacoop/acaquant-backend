"""api/services/ap5_posiciones.py — POSICIONES Y DIFERENCIAS.

Lo que la mesa manda por mail todos los días ("RESUMEN POSICIONES AGRO Y DÓLAR
FUTURO"), calculado server-side desde `ap5.portfolio` — o sea desde lo que dice
**la cámara**, no desde nuestro registro de boletos.

Reemplaza a la vieja tab DIFERENCIAS DIARIAS, que leía el TEXTO de
`operaciones.negocio_movimientos` (`"Diferencias diarias - [SOJ.ROS/MAY26] …"`).
Eran los mismos pesos contados por otro camino: ahí la diferencia aparecía
porque alguien la había registrado como movimiento, acá porque la cámara la
liquidó. Cuando los dos no coincidían, no había forma de saber cuál mandaba.

## Las cuatro cosas que no son obvias

**1. La cantidad viene en CONTRATOS.** El reporte habla de "Posición Tn Neta" y
`long_qty`/`short_qty` son contratos. Se multiplican por `ap5.contratos`, que el
job DERIVA de la identidad del settlement. No se puede usar `unit_of_measure`
como atajo: dentro de `Tn` conviven multiplicadores de 100, 10 y 5.

**2. Las monedas NO se suman.** El agro liquida en Dólar MtR y el dólar futuro en
Pesos. Todo total viaja partido por moneda; no existe un "total general".

**3. La diferencia del día ES `daily_settlement`.** No se calcula: la manda la
cámara. El acumulado sí se deriva (arrastre cargado + Σ de los días posteriores), igual
que el histórico de `/aca` — un acumulado guardado puede contradecir a sus
insumos y ahí no hay forma de saber cuál está bien.

**4. Lo que no encaja NO se esconde.** Un símbolo sin multiplicador, una cuenta
sin nombre o una unidad nueva salen declarados en la respuesta. La alternativa
—omitirlos— da un total plausible al que le falta algo.
"""
from __future__ import annotations

import unicodedata
from typing import Any

from config import (
    AP5_ACTIVO_INTEGRADO_FILTRA_CUENTAS,
    AP5_CONCEPTOS_ACTIVO_INTEGRADO,
    AP5_CONCEPTOS_REQUERIMIENTO,
    AP5_CUENTAS_REQUERIMIENTO,
    AP5_MARGENES_INVERTIR_SIGNO,
    AP5_REQUERIMIENTO_FILTRA_CUENTAS,
)
from core.postgres import get_pool

# Familias del reporte, derivadas de `unit_of_measure`. Es lo que separa los dos
# rankings: el agro se mide en toneladas y el dólar futuro en dólares.
#
# `otros` existe para que una unidad nueva (hoy `Bl`, el WTI) NO desaparezca en
# silencio ni se cuele adentro del agro inflando toneladas que no son toneladas.
AGRO, DOLAR, OTROS = "agro", "dolar", "otros"

_FAMILIA_SQL = """
    CASE p.unit_of_measure
        WHEN 'USD' THEN 'dolar'
        WHEN 'Tn'  THEN 'agro'
        ELSE 'otros'
    END
"""

# El producto: lo que va antes del punto en el agro (SOJ.ROS/NOV26 → SOJ) y las
# tres primeras letras en el dólar futuro (DLR082026 → DLR).
#
# El `%%` NO es un typo: psycopg usa `%s` para los parámetros, así que un `%`
# literal dentro del SQL se escribe doble o la query ni siquiera llega a la base.
_PRODUCTO_SQL = """
    CASE WHEN p.symbol LIKE '%%.%%' THEN split_part(p.symbol, '.', 1)
         ELSE left(p.symbol, 3) END
"""

# Etiqueta legible. El código sigue siendo la identidad — esto es solo cómo se
# muestra, y lo que no esté acá se muestra con su código en vez de ocultarse.
ETIQUETAS = {
    "MAI": "MAIZ", "SOJ": "SOJA", "TRI": "TRIGO",
    "SOY": "SOJA CME", "DLR": "DÓLAR", "WTI": "WTI",
}

# Cuántas cuentas por ranking. El reporte muestra 10 arriba y 10 abajo.
TOP = 10

SIN_GRUPO = "(sin grupo)"

# Las dos monedas del reporte y en qué columna de `ap5.acumulado` vive cada una.
# El agro liquida en Dólar MtR y el dólar futuro en Pesos: NUNCA se suman, y que
# sean dos columnas distintas es lo que hace que no haya dónde escribir la suma.
#
# Una moneda que no esté acá NO tiene columna: se declara en `faltantes` en vez
# de caer en una de las dos, que daría un total plausible y equivocado.
MONEDAS = {"Pesos": "acumulado_pesos", "Dólar MtR": "acumulado_mtr"}

# El arrastre que corresponde a la moneda de la fila. Un CASE y no un COALESCE:
# si la moneda no es ninguna de las dos, queda NULL (= sin arrastre conocido) y
# no se le presta el de la otra.
def _por_moneda(pesos: str, mtr: str) -> str:
    """La columna que le corresponde a la moneda de la fila. NUNCA la otra, ni
    la suma: el agro liquida en Dólar MtR y el dólar futuro en Pesos."""
    return (f"CASE p.settlement_currency "
            f"WHEN 'Pesos' THEN a.{pesos} WHEN 'Dólar MtR' THEN a.{mtr} END")


_ARRASTRE_SQL = _por_moneda("acumulado_pesos", "acumulado_mtr")
_MOVIMIENTO_SQL = _por_moneda("movimiento_pesos", "movimiento_mtr")
_TOTAL_SQL = _por_moneda("total_pesos", "total_mtr")

# La familia decide la TAB — y son SOLO DOS: agro (trigo, soja, maíz: todo lo
# que se mide en toneladas) y dólar futuro.
#
# ⚠️ `otros` (hoy el WTI, unidad `Bl`) **NO entra en ninguna de las dos** (regla
# del user, 2026-08-25: *«van a ser solo para los futuros de trigo soja y maíz;
# en agro no va a haber otros»*). Antes se plegaba adentro de AGRO y estaba mal:
# un barril no es una tonelada, y sumarlo ahí daría un ranking que parece bien.
#
# Pero NO desaparece en silencio: lo que queda fuera de las dos tabs se DECLARA
# en `faltantes` (ver `_faltantes`). Omitirlo sería peor que mostrarlo mal —
# una posición que no está en ninguna pantalla es una posición que nadie mira.
TAB_DE_FAMILIA = {AGRO: "agro", DOLAR: "dolar"}

# Los dos lados del reporte, y de qué lado va cada uno en la pantalla.
IZQUIERDA, DERECHA, OTRO_LADO = "izq", "der", "otro"
_LADO_POR_GRUPO = {"COOPERATIVAS": IZQUIERDA, "MUNDO ACA": DERECHA}


def normalizar_grupo(g: str) -> str:
    """La CLAVE del grupo: mayúsculas, sin acentos y sin espacios de más.

    ⚠️ Esto NO es adivinar identidad por el nombre (lo que prohíbe la REGLA #9):
    `COOPERATIVAS` y `Cooperativas` son el MISMO valor tipeado distinto, y
    plegar mayúsculas es lo único que se hace acá — no se deduce nada de
    prefijos ni sufijos.

    Y hace falta: comparar el string crudo contra una lista escrita a mano ya
    falló (2026-08-25). La base decía `COOPERATIVAS`, la pantalla buscaba
    `Cooperativas`, no matcheaba, y **todas** las cuentas caían en el bloque de
    "sin clasificar" — con los dos rankings correctos, uno al lado del otro, y
    el título equivocado. No falló nada: mostró lo mismo mal etiquetado.
    """
    base = unicodedata.normalize("NFKD", (g or "").strip().upper())
    return "".join(c for c in base if not unicodedata.combining(c))


def lado_de_grupo(g: str) -> str:
    """De qué lado de la pantalla va este grupo. Lo decide el BACKEND para que la
    vista no tenga que comparar strings — que es justo donde se rompió."""
    return _LADO_POR_GRUPO.get(normalizar_grupo(g), OTRO_LADO)


# El nombre a mostrar: manda lo que escribió una persona, después lo que dice la
# cámara, y si no hay ninguno el número — que es feo pero no es mentira.
_NOMBRE_SQL = "COALESCE(NULLIF(c.name, ''), NULLIF(c.denominacion, ''), p.account)"

# La cantidad en su UNIDAD (toneladas, dólares), no en contratos.
#
# ⚠️ El `JOIN` con los multiplicadores es LEFT y el default es NULL, no 1: un
# símbolo sin multiplicador conocido tiene que quedar VISIBLE como faltante. Con
# default 1 sumaría como si un contrato fuera una tonelada y el total saldría
# igual de plausible y 100 veces mal.
_LARGO = "p.long_qty * m.multiplicador"
_CORTO = "p.short_qty * m.multiplicador"


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or {})
        if cur.description is None:
            return []
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _f(v: Any) -> float | None:
    return None if v is None else float(v)


def _f0(v: Any) -> float:
    return 0.0 if v is None else float(v)


def fechas() -> list[dict]:
    """Días con posición, del más reciente al más viejo. El universo propio de
    esta vista: no se ancla al calendario de operaciones porque la cámara tiene
    el suyo (y puede ir rezagada)."""
    return [
        {"fecha": r["fecha"], "filas": r["filas"], "cuentas": r["cuentas"]}
        for r in _q(
            "SELECT to_char(business_date, 'YYYY-MM-DD') AS fecha, count(*) AS filas, "
            "count(DISTINCT account) AS cuentas FROM ap5.portfolio "
            "GROUP BY business_date ORDER BY business_date DESC"
        )
    ]


def _fecha_valida(fecha: str | None) -> tuple[str | None, str | None]:
    """(la fecha a mostrar, la anterior CON DATOS).

    La anterior no es "ayer": es el día hábil previo **que tenga posición**. Si
    se restara un día, un lunes compararía contra un domingo vacío y toda la
    columna "Acum. al Día Ant." daría cero sin que nada falle.
    """
    disponibles = [f["fecha"] for f in fechas()]
    if not disponibles:
        return None, None
    hoy = fecha if fecha in disponibles else disponibles[0]
    i = disponibles.index(hoy)
    return hoy, (disponibles[i + 1] if i + 1 < len(disponibles) else None)


def diferencias_del_dia(fecha: str) -> list[dict]:
    """"Diferencias ACA HOY": Σ `daily_settlement` del día, POR MONEDA.

    Partido por moneda y no en un total único porque el agro liquida en Dólar
    MtR y el dólar futuro en Pesos. Sumarlos daría un número sin significado.
    """
    return [
        {
            "moneda": r["moneda"] or "(sin moneda)",
            "familia": r["familia"],
            "importe": _f0(r["importe"]),
            "cuentas": r["cuentas"],
        }
        for r in _q(
            f"SELECT p.settlement_currency AS moneda, {_FAMILIA_SQL} AS familia, "
            "sum(p.daily_settlement) AS importe, count(DISTINCT p.account) AS cuentas "
            "FROM ap5.portfolio p WHERE p.business_date = %(f)s "
            "GROUP BY 1, 2 ORDER BY 2, 1",
            {"f": fecha},
        )
    ]


def rankings(filas: list[dict]) -> list[dict]:
    """Top N por cuenta, **UN bloque por (tab, grupo)** y ordenado por ACUMULADO.

    ⚠️ **Agrupa por TAB, no por familia.** La tab AGRO junta `agro` y `otros`; si
    agrupara por familia, un grupo con posiciones en las dos saldría DOS VECES en
    la misma pantalla, con el mismo título y sin que nada falle (pasó el
    2026-08-25). El bloque es lo que se dibuja, así que el bloque es la unidad.

    ⚠️ **El grupo se compara NORMALIZADO** (`normalizar_grupo`) y se muestra tal
    como está en la base. Comparar el string crudo ya mandó todo al bucket de
    "sin clasificar" porque la base dice `COOPERATIVAS` y el código buscaba
    `Cooperativas`.

    ⚠️ **No recibe una fecha: recibe las filas de `acumulado()`.** Es la misma
    lista que alimenta el resto de la vista, y eso es a propósito — si el ranking
    corriera su propia query, el día que las dos difieran la pantalla se
    contradiría a sí misma y las dos mitades seguirían siendo coherentes por
    separado. Es la REGLA #9(B) adentro de una vista.

    El `grupo` es una columna MANUAL de `ap5.cuentas`: la cámara no lo sabe y no
    se deduce del nombre. Lo no clasificado va a `(sin grupo)`, que se VE, en vez
    de repartirse a dedo entre los dos lados — si se escondiera, los totales no
    cerrarían contra el mail y nadie sabría por qué.
    """
    por_bloque: dict[tuple[str, str], list[dict]] = {}
    etiqueta: dict[tuple[str, str], str] = {}

    for r in filas:
        if not r["acumulado"]:
            continue
        tab = TAB_DE_FAMILIA.get(r["familia"])
        if tab is None:
            # Familia sin tab (hoy `otros`: el WTI). No se cuela en el agro
            # inflando toneladas — se cuenta en `faltantes` y se ve ahí.
            continue
        clave = (tab, normalizar_grupo(r["grupo"]))
        # La etiqueta que se muestra es la de la BASE, no la normalizada: el
        # normalizado es para comparar, no para dibujar.
        etiqueta.setdefault(clave, r["grupo"])
        por_bloque.setdefault(clave, []).append({
            "cuenta": r["cuenta"],
            "nombre": r["nombre"],
            "moneda": r["moneda"],
            "familia": r["familia"],
            "grupo": r["grupo"],
            # `importe` ES el acumulado: es lo que el ranking ordena.
            "importe": r["acumulado"],
            "diaria": r["diaria"],
            # Lo que el modal necesita para editar sin ir a buscarlo aparte.
            "arrastre": r["arrastre"],
            "cargado": r["cargado"],
            "fecha_arrastre": r["fecha_arrastre"],
        })

    salida = []
    for clave, items in por_bloque.items():
        tab, _ = clave
        grupo = etiqueta[clave]
        positivos = sorted([i for i in items if i["importe"] > 0],
                           key=lambda x: -x["importe"])
        negativos = sorted([i for i in items if i["importe"] < 0],
                           key=lambda x: x["importe"])
        salida.append({
            "tab": tab,
            "grupo": grupo,
            "lado": lado_de_grupo(grupo),
            # `total_*` es de TODAS las cuentas, no solo del top: el ranking
            # recorta la LISTA, no la suma. Si el total saliera del top, mostrar
            # 10 filas cambiaría el número — y nadie lo notaría.
            "positivos": positivos[:TOP],
            "negativos": negativos[:TOP],
            "total_positivo": round(sum(i["importe"] for i in positivos), 2),
            "total_negativo": round(sum(i["importe"] for i in negativos), 2),
            "cuentas": len(items),
            "sin_cargar": sum(1 for i in items if not i["cargado"]),
            # Cuántas filas tiene el ranking COMO MÁXIMO. Viaja para que la
            # pantalla reserve ese alto aunque haya menos cuentas: si cada panel
            # se encogiera a su cantidad de filas, Cooperativas y MUNDO ACA
            # quedarían de altos distintos y las tablas se desalinean.
            #
            # Va en el payload y no como un 10 escrito en el front: dos copias
            # del mismo número se separan solas (REGLA #9(B)) y el día que se
            # cambie el tope acá, la pantalla seguiría reservando el viejo.
            "top": TOP,
        })
    # Orden de la PANTALLA, fijado acá: izquierda, derecha, y lo no clasificado
    # último. Que lo decida el backend es lo que evita que la vista lo deduzca.
    orden = {IZQUIERDA: 0, DERECHA: 1, OTRO_LADO: 2}
    salida.sort(key=lambda b: (b["tab"], orden[b["lado"]], b["grupo"]))
    return salida


def por_instrumento(fecha: str, fecha_anterior: str | None) -> list[dict]:
    """El bloque POR INSTRUMENTO: la POSICIÓN, que se mide en CANTIDADES.

    Es lo que lo distingue del resto de la vista: acá no se suman importes sino
    contratos convertidos a su unidad — compra, venta y neta. El acumulado de
    diferencias viaja al lado porque es lo que el reporte pone en la misma fila,
    pero son dos cosas distintas y se calculan por separado.

    ⚠️ **El acumulado arranca en NUESTRO primer día.** La cámara manda la
    diferencia diaria, no el arrastre, así que lo anterior a la serie que
    guardamos no existe acá — vive en el arrastre de `ap5.acumulado`, que es por
    CUENTA. Por eso la respuesta declara `acumulado_desde`: un acumulado sin
    decir desde cuándo se lee como si fuera histórico completo.
    """
    filas = _q(
        f"SELECT {_FAMILIA_SQL} AS familia, {_PRODUCTO_SQL} AS producto, "
        "p.unit_of_measure AS unidad, p.settlement_currency AS moneda, "
        f"sum({_LARGO}) AS compra, sum({_CORTO}) AS venta, "
        "sum(p.long_qty) AS compra_contratos, sum(p.short_qty) AS venta_contratos, "
        "count(*) FILTER (WHERE m.multiplicador IS NULL) AS sin_multiplicador, "
        "sum(p.daily_settlement) AS diaria "
        "FROM ap5.portfolio p "
        "LEFT JOIN ap5.contratos m ON m.symbol = p.symbol "
        "WHERE p.business_date = %(f)s GROUP BY 1, 2, 3, 4 ORDER BY 1, 2",
        {"f": fecha},
    )

    # Acumulado por producto = Σ de TODOS los días guardados hasta la fecha. Se
    # calcula en una sola query y se cruza en memoria: son 5 productos.
    acum = {
        (r["familia"], r["producto"]): (_f0(r["hasta_hoy"]), _f0(r["hasta_ayer"]))
        for r in _q(
            f"SELECT {_FAMILIA_SQL} AS familia, {_PRODUCTO_SQL} AS producto, "
            "sum(p.daily_settlement) FILTER (WHERE p.business_date <= %(f)s) AS hasta_hoy, "
            "sum(p.daily_settlement) FILTER (WHERE p.business_date < %(f)s) AS hasta_ayer "
            "FROM ap5.portfolio p WHERE p.business_date <= %(f)s GROUP BY 1, 2",
            {"f": fecha},
        )
    }

    desde = _q("SELECT to_char(min(business_date), 'YYYY-MM-DD') AS d FROM ap5.portfolio")
    acumulado_desde = desde[0]["d"] if desde else None

    salida = []
    for r in filas:
        compra, venta = _f(r["compra"]), _f(r["venta"])
        neta = None if compra is None or venta is None else round(compra - venta, 2)
        hoy, ayer = acum.get((r["familia"], r["producto"]), (0.0, 0.0))
        salida.append({
            "familia": r["familia"],
            "producto": r["producto"],
            "etiqueta": ETIQUETAS.get(r["producto"], r["producto"]),
            "unidad": r["unidad"],
            "moneda": r["moneda"],
            "compra": None if compra is None else round(compra, 2),
            "venta": None if venta is None else round(venta, 2),
            "neta": neta,
            # Los contratos crudos viajan al lado a propósito: si un símbolo
            # queda sin multiplicador, la cantidad en unidad no se puede afirmar
            # y esto es lo único que queda para no perder la posición entera.
            "compra_contratos": _f0(r["compra_contratos"]),
            "venta_contratos": _f0(r["venta_contratos"]),
            "sin_multiplicador": r["sin_multiplicador"],
            "acum_hoy": round(hoy, 2),
            "acum_ayer": round(ayer, 2),
            "diaria": round(_f0(r["diaria"]), 2),
            "acumulado_desde": acumulado_desde,
            "fecha_anterior": fecha_anterior,
        })
    return salida


def _suma(items: list[dict], campo: str) -> float:
    """Σ de un campo, tratando el NULL como 0.

    Recibe `items` por parámetro y no lo captura del loop: una closure sobre la
    variable del ciclo se lee como si cada iteración tuviera la suya, y no es
    así (lo marcó ruff B023). Hoy anda porque se llama en la misma vuelta —
    justo la clase de bug que aguanta hasta que alguien mueve una línea.
    """
    return round(sum(x[campo] or 0 for x in items), 2)


def consolidado(fecha: str, fecha_anterior: str | None) -> list[dict]:
    """El bloque POR INSTRUMENTO del reporte: **un cuadro por tab**, con su TOTAL.

    Es la tabla que la mesa manda en el mail — FUTUROS AGRÍCOLAS arriba y
    FUTUROS U$S abajo — con la POSICIÓN (compra, venta, neta) y las diferencias
    acumuladas al día y al día anterior.

    **La posición es la sumatoria de las cantidades**, convertidas a su unidad:
    `Σ long_qty × multiplicador` y `Σ short_qty × multiplicador`. La neta es la
    resta. No es un importe — se mide en toneladas (agro) y en dólares (dólar
    futuro), que es justamente por qué cada tab tiene su cuadro y no hay uno solo.

    ⚠️ **El TOTAL se calcula ACÁ, no en el navegador.** Es la misma regla que
    rige en toda la vista: un contador sumado en el browser no se puede
    verificar del lado del servidor, y este cuadro se imprime.

    ⚠️ **Se agrupa por (tab, MONEDA).** Dentro de una tab la moneda suele ser
    una sola, pero no se asume: si aparecieran dos, salen dos cuadros en vez de
    un total que suma pesos con dólares. Un total mezclado da un número, no
    falla, y está mal.

    ⚠️ **"Acum. al día anterior" NO se guarda: se deriva** (Σ de las diferencias
    con `business_date < fecha`). Persistirlo sería un total que puede
    contradecir a sus propios insumos: el job es idempotente y re-corre, así que
    si un día se corrige el guardado no se entera. Derivado, corregir un día
    arregla todos los números de golpe. Misma decisión que el histórico de `/aca`.
    """
    # Se construye sobre las filas de `por_instrumento`: la MISMA query que ya
    # calcula posición y acumulados. Correr una propia abriría la puerta a que
    # el cuadro y el resto de la vista digan cosas distintas.
    return agrupar_consolidado(por_instrumento(fecha, fecha_anterior))


def agrupar_consolidado(filas: list[dict]) -> list[dict]:
    """La parte PURA de `consolidado()`: agrupar y totalizar, sin tocar la base.

    Está separada para poder congelarla con tests — es donde vive el criterio
    (qué se suma con qué) y donde un error no grita: un total mal agrupado sale
    prolijo y da otro número.
    """
    bloques: dict[tuple[str, str], list[dict]] = {}
    for r in filas:
        tab = TAB_DE_FAMILIA.get(r["familia"])
        if tab is None:
            continue  # `otros` (el WTI) no entra en ninguna tab — ver TAB_DE_FAMILIA
        bloques.setdefault((tab, r["moneda"] or "(sin moneda)"), []).append(r)

    salida = []
    for (tab, moneda), items in bloques.items():
        items.sort(key=lambda x: x["etiqueta"])
        # Las unidades que conviven en el cuadro. Si hay más de una, el total de
        # la posición no se puede afirmar y la vista tiene que poder decirlo.
        unidades = sorted({x["unidad"] for x in items if x["unidad"]})

        salida.append({
            "tab": tab,
            "moneda": moneda,
            "unidad": unidades[0] if len(unidades) == 1 else None,
            "unidades": unidades,
            "filas": items,
            "total": {
                "compra": _suma(items, "compra"),
                "venta": _suma(items, "venta"),
                "neta": _suma(items, "neta"),
                "acum_hoy": _suma(items, "acum_hoy"),
                "acum_ayer": _suma(items, "acum_ayer"),
                "diaria": _suma(items, "diaria"),
                # Si algún símbolo quedó sin multiplicador, la posición del
                # TOTAL está incompleta: se cuenta para poder decirlo.
                "sin_multiplicador": sum(x["sin_multiplicador"] for x in items),
            },
        })
    salida.sort(key=lambda b: (b["tab"] != "agro", b["moneda"]))
    return salida


def acumulado(fecha: str) -> list[dict]:
    """El acumulado por cuenta y moneda: el ARRASTRE cargado + Σ de lo posterior.

    El arrastre sale de `ap5.acumulado`, que tiene **una fila por cuenta y una
    columna por moneda** (`acumulado_pesos` / `acumulado_mtr`). Cada fila de acá
    toma la columna que le corresponde a SU moneda — nunca la otra, ni la suma
    de las dos: el agro liquida en Dólar MtR y el dólar futuro en Pesos, y
    sumarlos daría un número sin significado.

    ⚠️ **`fecha` es EXCLUSIVA**: el arrastre ya contiene todo hasta ese día
    inclusive, así que se suman los días POSTERIORES. Incluirlo lo contaría dos
    veces; y ponerle el primer día de la serie haría que ese día deje de contar.

    ⚠️ **`actualizado` en NULL = nadie lo cargó.** No es lo mismo que un
    arrastre de cero, y la diferencia importa: esta vista se imprime para
    gerencia, donde un cero que nadie escribió se lee igual que uno verificado.

    ⚠️ **El total se LEE de `ap5.acumulado.total_*`, no se suma acá.** Lo calcula
    el job en una sola sentencia (`arrastre + Σ daily_settlement`) y lo guarda.
    Antes se derivaba en cada lectura: el número era correcto pero **no había
    forma de auditarlo** — para saber por qué una cuenta mostraba lo que mostraba
    había que rehacer la suma a mano. Ahora las tres piezas están en la tabla y
    la cuenta se verifica a ojo.

    Lo que hace seguro guardarlo es que el job **recalcula el movimiento entero**
    en cada corrida en vez de acumularlo: correr cuatro veces el mismo día deja
    el mismo número. Un acumulador que suma lo del día al valor guardado daría el
    cuádruple y no fallaría nada.
    """
    filas = _q(
        f"""
        SELECT p.account AS cuenta,
               {_NOMBRE_SQL} AS nombre,
               COALESCE(NULLIF(c.grupo, ''), %(sin)s) AS grupo,
               p.settlement_currency AS moneda,
               {_FAMILIA_SQL} AS familia,
               {_ARRASTRE_SQL} AS arrastre,
               to_char(a.fecha, 'YYYY-MM-DD') AS fecha_arrastre,
               a.actualizado,
               {_MOVIMIENTO_SQL} AS movimiento,
               {_TOTAL_SQL} AS acumulado,
               a.movimiento_dias AS dias,
               to_char(a.movimiento_desde, 'YYYY-MM-DD') AS desde,
               to_char(a.movimiento_hasta, 'YYYY-MM-DD') AS hasta,
               sum(p.daily_settlement) FILTER (WHERE p.business_date = %(f)s) AS diaria
        FROM ap5.portfolio p
        LEFT JOIN ap5.cuentas c   ON c.account = p.account
        LEFT JOIN ap5.acumulado a ON a.account = p.account
        WHERE p.business_date <= %(f)s
        GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13
        ORDER BY 2, 4
        """,
        {"f": fecha, "sin": SIN_GRUPO},
    )
    salida = []
    for r in filas:
        arrastre = _f0(r["arrastre"])
        movimiento = _f0(r["movimiento"])
        salida.append({
            "cuenta": r["cuenta"],
            "nombre": r["nombre"],
            "grupo": r["grupo"],
            "moneda": r["moneda"],
            "familia": r["familia"],
            "arrastre": round(arrastre, 2),
            # Lo puso una PERSONA, o todavía nadie. Un arrastre en 0 sin cargar
            # y uno en 0 verificado dan el mismo número y son cosas distintas.
            "cargado": r["actualizado"] is not None,
            "fecha_arrastre": r["fecha_arrastre"],
            "actualizado": r["actualizado"].isoformat() if r["actualizado"] else None,
            "movimiento": round(movimiento, 2),
            # ⚠️ Se LEE de la tabla, no se suma acá. El job lo calcula y lo
            # guarda; si lo recalculáramos en la lectura, la pantalla podría
            # decir un número y la tabla otro, y no habría cómo saber cuál manda.
            "acumulado": round(_f0(r["acumulado"]), 2),
            "movimiento_dias": int(r["dias"] or 0),
            "movimiento_desde": r["desde"],
            "movimiento_hasta": r["hasta"],
            "diaria": round(_f0(r["diaria"]), 2),
        })
    return salida


def _faltantes(fecha: str) -> dict:
    """Lo que la vista no puede afirmar. Se declara, no se esconde."""
    sin_mult = _q(
        "SELECT p.symbol, p.unit_of_measure AS unidad, count(*) AS filas "
        "FROM ap5.portfolio p LEFT JOIN ap5.contratos m ON m.symbol = p.symbol "
        "WHERE p.business_date = %(f)s AND m.symbol IS NULL "
        "GROUP BY 1, 2 ORDER BY 1",
        {"f": fecha},
    )
    resto = _q(
        "SELECT count(*) FILTER (WHERE c.name IS NULL AND c.denominacion IS NULL) AS sin_nombre, "
        "count(*) FILTER (WHERE c.grupo IS NULL OR c.grupo = '') AS sin_grupo, "
        "count(*) AS cuentas FROM ap5.cuentas c "
        "WHERE c.account IN (SELECT account FROM ap5.portfolio WHERE business_date = %(f)s)",
        {"f": fecha},
    )
    # Lo que NO entra en ninguna tab (hoy `otros`: el WTI, que se mide en
    # barriles). Se cuenta para que una posición sin pantalla no quede invisible.
    fuera = _q(
        f"SELECT {_FAMILIA_SQL} AS familia, count(DISTINCT p.account) AS cuentas, "
        "count(DISTINCT p.symbol) AS simbolos "
        "FROM ap5.portfolio p WHERE p.business_date = %(f)s "
        "GROUP BY 1 HAVING " + _FAMILIA_SQL.strip() + " NOT IN ('agro', 'dolar')",
        {"f": fecha},
    )

    r = resto[0] if resto else {}
    return {
        "fuera_de_tabs": [
            {"familia": x["familia"], "cuentas": x["cuentas"], "simbolos": x["simbolos"]}
            for x in fuera
        ],
        "simbolos_sin_multiplicador": [
            {"symbol": s["symbol"], "unidad": s["unidad"], "filas": s["filas"]}
            for s in sin_mult
        ],
        "cuentas_sin_nombre": r.get("sin_nombre", 0),
        "cuentas_sin_grupo": r.get("sin_grupo", 0),
        "cuentas": r.get("cuentas", 0),
        # Cuentas cuyo ARRASTRE no cargó nadie (`actualizado` en NULL o sin
        # fila). Un cero que nadie escribió se lee igual que uno verificado, y
        # esta vista se imprime.
        "cuentas_sin_cargar": _q(
            "SELECT count(DISTINCT p.account) AS n FROM ap5.portfolio p "
            "LEFT JOIN ap5.acumulado a ON a.account = p.account "
            "WHERE p.business_date = %(f)s AND a.actualizado IS NULL",
            {"f": fecha},
        )[0]["n"],
    }


_CARD_VACIA: dict[str, Any] = {
    "fecha": None, "por_moneda": [], "por_concepto": [], "detalle": [],
    "conceptos": [],
    "conceptos_faltantes": [], "filtra_cuentas": False, "cuentas_pedidas": 0,
    "cuentas_encontradas": 0, "cuentas_faltantes": [],
}


def _card_margenes(fecha: str, conceptos: tuple[str, ...], *,
                   filtra_cuentas: bool) -> dict:
    """Σ de `ap5.margenes` para estos conceptos, por moneda.

    Una sola función para las dos cards porque son **la misma cuenta con otro
    recorte**. Pero el recorte tiene DOS ejes, no uno, y ahí estuvo el error:

        REQUERIMIENTO    `Márgenes`               filtra cuentas → lo exigido a
                                                  NUESTRAS dos cuentas
        ACTIVO INTEGRADO `Márgenes + Inicial A3`  NO filtra → lo depositado por
                                                  el ALyC entero

    ⚠️ **Por qué el activo integrado no filtra**: `Inicial A3` es UNA sola fila y
    no cuelga de ningún comitente. Filtrándola se caía, y la card quedaba
    sumando exactamente lo mismo que el requerimiento — **un número correcto
    para las filas que encontró**, que es la peor forma de estar mal.

    ⚠️ **Se suma `margen` (`Margin`) y NADA MÁS.** `primas` e `inter_temporal`
    se guardan pero no cuentan: `Márgenes` trae un `InterTempAmount` no nulo que
    NO es parte del número.

    ⚠️ **`conceptos_faltantes` viaja en la respuesta.** Si la cámara renombra
    `Inicial A3`, la card seguiría dibujando el total de los que sí quedaron y
    nadie lo notaría. Lo cuenta el backend contra la base, no el navegador.
    """
    pares = list(AP5_CUENTAS_REQUERIMIENTO) if filtra_cuentas else []
    vacia = {"fecha": fecha, "por_moneda": [], "por_concepto": [], "detalle": [],
             "conceptos": list(conceptos), "conceptos_faltantes": list(conceptos),
             "filtra_cuentas": filtra_cuentas,
             "cuentas_pedidas": len(pares), "cuentas_encontradas": 0,
             "cuentas_faltantes": [c for c, _ in pares]}
    if not fecha or not conceptos or (filtra_cuentas and not pares):
        return vacia

    sql = ("SELECT cuenta, cuenta_compensacion, concepto, moneda, "
           "       margen, primas, inter_temporal, referencias, titular "
           "FROM ap5.margenes "
           "WHERE fecha = %(f)s AND concepto = ANY(%(conc)s::text[]) ")
    params: dict[str, Any] = {"f": fecha, "conc": list(conceptos)}
    if filtra_cuentas:
        sql += ("  AND (cuenta, cuenta_compensacion) IN "
                "      (SELECT * FROM unnest(%(ctas)s::text[], %(comps)s::text[])) ")
        params["ctas"] = [c for c, _ in pares]
        params["comps"] = [k for _, k in pares]
    filas = _q(sql + "ORDER BY cuenta, concepto, moneda", params)

    signo = -1.0 if AP5_MARGENES_INVERTIR_SIGNO else 1.0
    por: dict[str, dict] = {}
    for x in filas:
        m = x["moneda"] or "(sin moneda)"
        d = por.setdefault(m, {"moneda": m, "importe": 0.0, "filas": 0})
        d["importe"] += float(x["margen"] or 0) * signo
        d["filas"] += 1

    # Cuánto aportó CADA concepto. Sin esto, «`Inicial A3` sumó 0» y «`Inicial
    # A3` no entró en la query» dan el mismo total y se ven idénticos en la
    # pantalla — que es exactamente cómo se perdieron dos vueltas acá.
    porcon: dict[tuple[str, str], dict] = {}
    for x in filas:
        k = (x["concepto"], x["moneda"] or "(sin moneda)")
        d = porcon.setdefault(k, {"concepto": k[0], "moneda": k[1],
                                  "importe": 0.0, "filas": 0})
        d["importe"] += float(x["margen"] or 0) * signo
        d["filas"] += 1

    hallada = {(x["cuenta"], x["cuenta_compensacion"]) for x in filas}
    presentes = {x["concepto"] for x in filas}
    return {
        "fecha": fecha,
        "por_moneda": [{**d, "importe": round(d["importe"], 2)}
                       for d in sorted(por.values(), key=lambda x: x["moneda"])],
        "por_concepto": [{**d, "importe": round(d["importe"], 2)}
                         for d in sorted(porcon.values(),
                                         key=lambda x: (x["moneda"], x["concepto"]))],
        "detalle": [{**x, "importe": round(float(x["margen"] or 0) * signo, 2),
                     "margen": float(x["margen"] or 0),
                     "primas": float(x["primas"] or 0),
                     "inter_temporal": float(x["inter_temporal"] or 0)}
                    for x in filas],
        "conceptos": list(conceptos),
        "conceptos_faltantes": [c for c in conceptos if c not in presentes],
        "filtra_cuentas": filtra_cuentas,
        # Sin filtro no hay «cuentas pedidas»: cero, y por lo tanto ninguna
        # puede faltar. Mandar la lista igual haría que la pantalla avise por
        # cuentas que esta card nunca miró.
        "cuentas_pedidas": len(pares),
        "cuentas_encontradas": len(hallada) if filtra_cuentas else 0,
        "cuentas_faltantes": ([c for c, k in pares if (c, k) not in hallada]
                              if filtra_cuentas else []),
    }


def requerimiento_margenes(fecha: str) -> dict:
    """La card REQUERIMIENTO DE MÁRGENES: lo exigido a NUESTRAS dos cuentas."""
    return _card_margenes(fecha, AP5_CONCEPTOS_REQUERIMIENTO,
                          filtra_cuentas=AP5_REQUERIMIENTO_FILTRA_CUENTAS)


def activo_integrado(fecha: str) -> dict:
    """La card ACTIVO INTEGRADO: lo depositado, a nivel ALyC.

    Sale de la MISMA respuesta que los márgenes (`MarginRequirementReport`),
    sumando `Márgenes + Inicial A3` y **sin filtrar cuentas**. `AccountBalance`,
    que parecía el método natural, quedó descartado: da un agregado por cuenta
    de compensación que no se puede abrir por comitente (5 filtros y 10 grafías
    de expansión, las 15 devuelven lo mismo).
    """
    return _card_margenes(fecha, AP5_CONCEPTOS_ACTIVO_INTEGRADO,
                          filtra_cuentas=AP5_ACTIVO_INTEGRADO_FILTRA_CUENTAS)


def actualizado(fecha: str) -> dict:
    """CUÁNDO se tocó por última vez cada insumo de la vista.

    ⚠️ **Es el único dato de esta pantalla que NO se puede derivar mirando los
    números.** Un job que no corrió deja los datos de ayer, y en la pantalla eso
    se ve exactamente igual que un día sin movimiento: las mismas filas, los
    mismos totales, cero señales. Sin el sello, «miré y estaba todo igual» y «el
    job murió el jueves» son indistinguibles — que es la misma razón por la que
    el AV AGENT muestra la última corrida de cada habilidad al lado de sus
    hallazgos.

    Son TRES relojes distintos y por eso se muestran separados: la posición y
    los márgenes los trae el job de las 10, y el arrastre lo carga una persona
    cuando puede. Un solo «actualizado» tendría que elegir uno y taparía a los
    otros dos.
    """
    r = _q(
        """
        SELECT (SELECT max(actualizado_at) FROM ap5.portfolio
                WHERE business_date = %(f)s)                    AS posicion,
               (SELECT max(actualizado_at) FROM ap5.margenes
                WHERE fecha = %(f)s)                            AS margenes,
               (SELECT max(actualizado)    FROM ap5.acumulado)  AS arrastre
        """,
        {"f": fecha},
    )
    d = r[0] if r else {}
    return {k: (v.isoformat() if v else None)
            for k, v in (("posicion", d.get("posicion")),
                         ("margenes", d.get("margenes")),
                         ("arrastre", d.get("arrastre")))}


def vista(fecha: str | None = None) -> dict:
    """TODO lo que la pantalla necesita, en UN request.

    Un solo endpoint y no cinco porque cada roundtrip a Supabase paga ~8,5 ms de
    peaje fijo por distancia (medido) — y porque los bloques tienen que hablar de
    la MISMA fecha: con llamadas separadas, un job corriendo en el medio dejaría
    el resumen en un día y el ranking en otro, sin que nada falle.
    """
    hoy, anterior = _fecha_valida(fecha)
    if not hoy:
        return {
            "fecha": None, "fecha_anterior": None, "fechas": [],
            "diferencias_hoy": [], "rankings": [], "por_instrumento": [],
            "consolidado": [],
            "acumulado": [], "grupos": [], "faltantes": {},
            "requerimiento_margenes": _CARD_VACIA,
            "activo_integrado": _CARD_VACIA,
        }

    # UNA sola vez, y de ahí salen las DOS cosas que hablan de lo mismo: la tabla
    # de acumulado y el ranking que la ordena. Calcularlo dos veces sería abrir
    # la puerta a que se contradigan.
    filas_acum = acumulado(hoy)

    return {
        "fecha": hoy,
        "fecha_anterior": anterior,
        "fechas": fechas(),
        "diferencias_hoy": diferencias_del_dia(hoy),
        "rankings": rankings(filas_acum),
        "por_instrumento": por_instrumento(hoy, anterior),
        "consolidado": consolidado(hoy, anterior),
        "acumulado": filas_acum,
        "requerimiento_margenes": requerimiento_margenes(hoy),
        "activo_integrado": activo_integrado(hoy),
        "grupos": [
            r["grupo"] for r in _q(
                "SELECT DISTINCT grupo FROM ap5.cuentas "
                "WHERE grupo IS NOT NULL AND grupo <> '' ORDER BY grupo")
        ],
        "faltantes": _faltantes(hoy),
        "actualizado": actualizado(hoy),
    }


# --------------------------------------------------------------------------- #
# Escrituras — las DOS cosas que ninguna fuente sabe y carga una persona
# --------------------------------------------------------------------------- #
def guardar_cuenta(account: str, *, name: str | None = None,
                   grupo: str | None = None, por: str = "") -> dict:
    """Nombre propio y GRUPO de una cuenta.

    `name` no pisa a `denominacion`: son columnas distintas justamente para que
    la corrección humana y el dato de la cámara puedan convivir. Mandar vacío
    BORRA el override y vuelve a mandar el de la cámara, que es la forma de
    deshacer sin tener que saber cuál era el nombre original.
    """
    sets, params = [], {"account": account, "por": por}
    if name is not None:
        sets.append("name = NULLIF(%(name)s, '')")
        params["name"] = name.strip()
    if grupo is not None:
        sets += ["grupo = NULLIF(%(grupo)s, '')", "grupo_por = %(por)s", "grupo_at = now()"]
        params["grupo"] = grupo.strip()
    if not sets:
        return {"ok": False, "motivo": "nada para actualizar"}

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ap5.cuentas (account) VALUES (%(account)s) ON CONFLICT DO NOTHING",
            {"account": account},
        )
        cur.execute(f"UPDATE ap5.cuentas SET {', '.join(sets)} WHERE account = %(account)s",
                    params)
    return {"ok": True, "account": account}


def guardar_acumulado(account: str, acumulado_pesos: float, acumulado_mtr: float,
                      fecha: str, *, por: str = "") -> dict:
    """El ARRASTRE de una cuenta, en sus DOS monedas.

    Una fila por cuenta y las dos monedas juntas: el agro liquida en Dólar MtR y
    el dólar futuro en Pesos, y tenerlas en columnas separadas es lo que impide
    que alguien las sume.

    ⚠️ **`fecha` es EXCLUSIVA**: los dos importes ya contienen todo hasta ese día
    inclusive, y el sistema suma los días POSTERIORES. Ponerle el primer día de
    la serie haría que ese día deje de contar y el acumulado bajaría sin que nada
    falle.

    `actualizado` se sella acá — es lo que distingue un arrastre que puso una
    persona de un cero que dejó el sembrador.
    """
    if not fecha:
        return {"ok": False, "motivo": "la fecha es obligatoria"}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ap5.acumulado (account, acumulado_pesos, acumulado_mtr, "
            "fecha, actualizado, cargado_por) "
            "VALUES (%(a)s, %(p)s, %(m)s, %(f)s, now(), %(por)s) "
            "ON CONFLICT (account) DO UPDATE SET "
            "acumulado_pesos = EXCLUDED.acumulado_pesos, "
            "acumulado_mtr = EXCLUDED.acumulado_mtr, "
            "fecha = EXCLUDED.fecha, actualizado = now(), "
            "cargado_por = EXCLUDED.cargado_por",
            {"a": account, "p": acumulado_pesos, "m": acumulado_mtr,
             "f": fecha, "por": por},
        )
    return {"ok": True, "account": account}


def cuentas() -> list[dict]:
    """El padrón para el ABM: número, los DOS nombres y el grupo."""
    return [
        {
            "cuenta": r["account"],
            "name": r["name"],
            "denominacion": r["denominacion"],
            "nombre": r["name"] or r["denominacion"] or r["account"],
            "cuit": r["cuit"],
            "grupo": r["grupo"],
            "visto_at": r["visto_at"].isoformat() if r["visto_at"] else None,
        }
        for r in _q(
            "SELECT account, name, denominacion, cuit, grupo, visto_at "
            "FROM ap5.cuentas ORDER BY COALESCE(NULLIF(name, ''), "
            "NULLIF(denominacion, ''), account)"
        )
    ]
