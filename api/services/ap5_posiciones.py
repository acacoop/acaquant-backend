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
cámara. El acumulado sí se deriva (semilla + Σ de los días posteriores), igual
que el histórico de `/aca` — un acumulado guardado puede contradecir a sus
insumos y ahí no hay forma de saber cuál está bien.

**4. Lo que no encaja NO se esconde.** Un símbolo sin multiplicador, una cuenta
sin nombre o una unidad nueva salen declarados en la respuesta. La alternativa
—omitirlos— da un total plausible al que le falta algo.
"""
from __future__ import annotations

import unicodedata
from typing import Any

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

# La familia decide la TAB. `otros` (hoy el WTI, unidad `Bl`) NO tiene tab propia
# y tampoco desaparece: va con el agro, pero conserva su etiqueta de familia para
# que se vea que no son toneladas.
TAB_DE_FAMILIA = {AGRO: "agro", OTROS: "agro", DOLAR: "dolar"}

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
        tab = TAB_DE_FAMILIA.get(r["familia"], "agro")
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
            "semilla": r["semilla"],
            "semilla_cargada": r["semilla_cargada"],
            "desde_fecha": r["desde_fecha"],
            "nota": r["nota"],
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
            "sin_semilla": sum(1 for i in items if not i["semilla_cargada"]),
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
    guardamos no existe acá — vive en la semilla de `ap5.acumulado`, que es por
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


def acumulado(fecha: str) -> list[dict]:
    """El ACUMULADO por cuenta: semilla manual + Σ de las diferencias posteriores.

    ⚠️ **La clave lleva la moneda**, y eso se midió: cuatro cuentas (221369,
    222812, 229540, 229664) tienen agro en Dólar MtR y dólar futuro en Pesos a la
    vez. Con clave solo por cuenta, el acumulado sumaría pesos con dólares — daría
    un número, no fallaría nada, y estaría mal.

    El total NO se persiste: se deriva acá. Un acumulado guardado puede
    contradecir a sus propios insumos y ahí no hay forma de saber cuál manda
    (misma decisión que el histórico de `/aca`).

    `desde_fecha` de la semilla es EXCLUSIVA: la semilla ya contiene el arrastre
    hasta ese día inclusive, así que se suman los días POSTERIORES. Incluirlo lo
    contaría dos veces.
    """
    filas = _q(
        f"""
        SELECT p.account AS cuenta,
               {_NOMBRE_SQL} AS nombre,
               COALESCE(NULLIF(c.grupo, ''), %(sin)s) AS grupo,
               p.settlement_currency AS moneda,
               {_FAMILIA_SQL} AS familia,
               a.semilla,
               to_char(a.desde_fecha, 'YYYY-MM-DD') AS desde_fecha,
               a.nota,
               sum(p.daily_settlement) FILTER (
                   WHERE a.desde_fecha IS NULL OR p.business_date > a.desde_fecha
               ) AS movimiento,
               sum(p.daily_settlement) FILTER (WHERE p.business_date = %(f)s) AS diaria
        FROM ap5.portfolio p
        LEFT JOIN ap5.cuentas c   ON c.account = p.account
        LEFT JOIN ap5.acumulado a ON a.account = p.account
                                 AND a.currency = p.settlement_currency
        WHERE p.business_date <= %(f)s
        GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
        ORDER BY 2, 4
        """,
        {"f": fecha, "sin": SIN_GRUPO},
    )
    salida = []
    for r in filas:
        semilla = _f(r["semilla"])
        movimiento = _f0(r["movimiento"])
        salida.append({
            "cuenta": r["cuenta"],
            "nombre": r["nombre"],
            "grupo": r["grupo"],
            "moneda": r["moneda"],
            "familia": r["familia"],
            # `semilla = None` NO es cero: es "nadie cargó el arrastre todavía".
            # Se distinguen porque el acumulado de una cuenta sin semilla es
            # incompleto y la vista tiene que poder decirlo.
            "semilla": semilla,
            "semilla_cargada": semilla is not None,
            "desde_fecha": r["desde_fecha"],
            "nota": r["nota"],
            "movimiento": round(movimiento, 2),
            "acumulado": round((semilla or 0.0) + movimiento, 2),
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
    r = resto[0] if resto else {}
    return {
        "simbolos_sin_multiplicador": [
            {"symbol": s["symbol"], "unidad": s["unidad"], "filas": s["filas"]}
            for s in sin_mult
        ],
        "cuentas_sin_nombre": r.get("sin_nombre", 0),
        "cuentas_sin_grupo": r.get("sin_grupo", 0),
        "cuentas": r.get("cuentas", 0),
        "cuentas_sin_semilla": _q(
            "SELECT count(*) AS n FROM ("
            "  SELECT DISTINCT p.account, p.settlement_currency FROM ap5.portfolio p"
            "  WHERE p.business_date = %(f)s) t "
            "LEFT JOIN ap5.acumulado a ON a.account = t.account "
            "  AND a.currency = t.settlement_currency WHERE a.account IS NULL",
            {"f": fecha},
        )[0]["n"],
    }


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
            "acumulado": [], "grupos": [], "faltantes": {},
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
        "acumulado": filas_acum,
        "grupos": [
            r["grupo"] for r in _q(
                "SELECT DISTINCT grupo FROM ap5.cuentas "
                "WHERE grupo IS NOT NULL AND grupo <> '' ORDER BY grupo")
        ],
        "faltantes": _faltantes(hoy),
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


def guardar_semilla(account: str, currency: str, semilla: float,
                    desde_fecha: str, *, nota: str = "", por: str = "") -> dict:
    """La semilla del acumulado de una cuenta en UNA moneda.

    `currency` es obligatoria y no tiene default a propósito: una semilla sin
    moneda es exactamente el error que la PK viene a impedir.
    """
    if not currency:
        return {"ok": False, "motivo": "la moneda es obligatoria"}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ap5.acumulado (account, currency, semilla, desde_fecha, nota, "
            "cargado_por, actualizado_at) VALUES (%(a)s, %(c)s, %(s)s, %(d)s, "
            "NULLIF(%(n)s, ''), %(p)s, now()) "
            "ON CONFLICT (account, currency) DO UPDATE SET "
            "semilla = EXCLUDED.semilla, desde_fecha = EXCLUDED.desde_fecha, "
            "nota = EXCLUDED.nota, cargado_por = EXCLUDED.cargado_por, "
            "actualizado_at = now()",
            {"a": account, "c": currency, "s": semilla, "d": desde_fecha,
             "n": nota.strip(), "p": por},
        )
    return {"ok": True, "account": account, "currency": currency}


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
