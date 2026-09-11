"""asistente/herramientas.py — LO QUE EL ASISTENTE SABE HACER.

── SU ROL EN EL CICLO ──

Acá viven las funciones que el modelo puede PEDIR. Son funciones de Python
normales: no tienen decoradores, no se registran solas, no hay magia. El modelo
nunca las ejecuta ni las ve — recibe una FICHA de cada una (nombre, para qué
sirve, qué argumentos toma) y con eso decide cuál quiere.

Quien las ejecuta es el ciclo (`asistente/ciclo.py`), no el modelo.

⚠️ **EL DOCSTRING ES EL PROMPT.** La descripción que el modelo lee para decidir
si usa una herramienta es, literalmente, el docstring de la función — lo arma
`ficha()` acá abajo. No es documentación para un programador: es la instrucción
con la que el modelo elige. Si el modelo llama a la herramienta equivocada, o
con los argumentos equivocados, **el bug está en el docstring**.
"""
from __future__ import annotations

import inspect
from datetime import date

from asistente import permitido
from core.postgres import get_pool

# Tope de cuántos días para adelante se puede preguntar. No es capricho: sin
# esto el modelo puede pedir 100.000 días y traerse el catálogo entero a la
# conversación, que se paga por token.
MAX_DIAS = 730


def bonos_que_vencen(dias: int) -> dict:
    """Los bonos que tenemos en cartera y vencen dentro de los próximos N días.

    Devuelve, por cada bono: el ticker, la fecha exacta de vencimiento, cuántos
    nominales tenemos, la valuación, en qué moneda está valuado y en cuántas
    cuentas distintas aparece. Ordenados del que vence primero al último.

    Sirve para saber qué hay que rollear y con cuánta anticipación. NO dice qué
    comprar en su lugar: para eso hace falta mirar el mercado, que es otra
    herramienta.

    ⚠️ SOLO mira las cuentas habilitadas para el asistente, que vienen en la
    respuesta (`cuentas_miradas`). NO es toda la cartera de la casa: si te
    preguntan por una cuenta que no está en esa lista, decí que no tenés acceso
    a esa cuenta, en vez de contestar con lo que sí ves.

    La cartera se lee de la ÚLTIMA foto de tenencia disponible, y se cuentan
    solo las posiciones que suman al AuM. La fecha de esa foto viene en la
    respuesta (`foto_del`): si es de hace varios días, decilo al contestar.

    Un bono cuyo vencimiento no está cargado NO aparece en la lista — se
    informa aparte en `sin_vencimiento_cargado`, para que no se lea como "no
    vence nada". Esa lista trae SOLO bonos: el efectivo, las acciones y los
    ETFs de la cartera no están ahí, porque no les falta un dato — no tienen
    vencimiento por lo que son.

    Si un bono viene con `moneda: "SIN DATO en la tenencia"`, decilo al dar su
    valuación. Un número de plata sin moneda no se puede comparar con otro.

    Args:
        dias: cuántos días para adelante mirar. Entre 1 y 730.
    """
    try:
        n = int(dias)
    except (TypeError, ValueError):
        return {"error": f"`dias` tiene que ser un número entero, llegó {dias!r}"}
    if n < 1 or n > MAX_DIAS:
        return {"error": f"`dias` tiene que estar entre 1 y {MAX_DIAS}, llegó {n}",
                "que_hacer": "Volvé a llamar con un número dentro de ese rango."}

    # ⚠️⚠️ **EL PERMISO VA PRIMERO Y CORTA.** Si no hay ninguna cuenta
    # habilitada, la consulta NO sale. `permitido.parametros()` levanta, y acá
    # se convierte en un dato que el modelo puede contar — no en una lista
    # vacía, que se leería como "no tenés nada" y es mentira.
    try:
        params = permitido.parametros()
    except permitido.SinPermiso:
        return permitido.como_error()
    params["d"] = n

    # ⚠️ **EL VENCIMIENTO SALE DE `mercado.curvas`, NO DE `portafolio.assets`.**
    # `assets.vencimiento` es TEXTO libre; `curvas.fecha_vencimiento` es una
    # fecha de verdad, o sea que la puede comparar la base y no hay que
    # parsear nada. Comparar fechas escritas a mano es cómo se cuelan errores
    # que no fallan: "2027-3-5" y "05/03/2027" ordenan distinto.
    #
    # La cadena para llegar es `tenencia.unidad → assets.unidad → assets.ticker
    # → curvas.ticker`, que es el mismo join que ya usan `validar_instrumentos`,
    # `core/duplicados` y `agente/vigencia`. NO se joinea por ticker suelto
    # desde tenencia: ahí el ticker puede traer la especie (AL30D) y el del
    # catálogo es el base (AL30).
    #
    # `{permiso}` es `permitido.FILTRO_SQL`, y va en las DOS consultas. La foto
    # también se busca dentro de las cuentas permitidas: la última fecha de
    # otra cuenta no dice nada de éstas.
    sql = f"""
        WITH foto AS (
            SELECT max(fecha) AS f FROM portafolio.tenencia t
             WHERE aum = 'si' AND {permitido.FILTRO_SQL}
        )
        SELECT a.ticker,
               c.fecha_vencimiento,
               sum(t.cantidad)             AS nominales,
               sum(t.valuacion)            AS valuacion,
               t.moneda,
               count(DISTINCT t.id_cuenta) AS cuentas,
               min(t.fecha)                AS foto_del
          FROM portafolio.tenencia t
          JOIN foto ON t.fecha = foto.f
          JOIN portafolio.assets a ON a.unidad = t.unidad
          JOIN mercado.curvas   c ON c.ticker = a.ticker
         WHERE t.aum = 'si'
           AND {permitido.FILTRO_SQL}
           AND c.fecha_vencimiento IS NOT NULL
           AND c.fecha_vencimiento >= current_date
           AND c.fecha_vencimiento <= current_date + make_interval(days => %(d)s)
         -- ⚠️ La moneda va en el GROUP BY y no se suma por arriba: sumar pesos
         -- con dólares da un número que parece bueno y no lo es. Si un ticker
         -- aparece en dos monedas, son dos filas y el que lee lo ve.
         GROUP BY a.ticker, c.fecha_vencimiento, t.moneda
         ORDER BY c.fecha_vencimiento, a.ticker
    """
    # Los BONOS de esas cuentas que no tienen el vencimiento cargado. Va aparte
    # y se informa: sin esto, un bono con la ficha incompleta desaparece de la
    # respuesta y se lee como "ese no vence".
    #
    # ⚠️⚠️ **EL JOIN ES INNER Y ESA ES LA CORRECCIÓN.** Con `LEFT JOIN` entraba
    # acá TODO lo que no matcheara contra el catálogo de renta fija: en la
    # primera corrida real devolvió ARS, USD y USDC (efectivo), MSFT y RKLB
    # (acciones) e IBIT y ETHA (ETFs). El modelo lo repitió tal cual —«sin
    # vencimiento cargado: ARS, ETHA, IBIT…»— y eso es falso: no les falta el
    # dato, es que no son bonos.
    #
    # `mercado.curvas` ES el catálogo de renta fija (su columna `tipo` es
    # Bono / Lecap / Boncap / Soberano / ON). Estar ahí adentro es la
    # definición de "es un bono", así que el INNER JOIN alcanza y no hace falta
    # una lista de clases de activo que alguien tenga que mantener.
    sql_sin = f"""
        WITH foto AS (
            SELECT max(fecha) AS f FROM portafolio.tenencia t
             WHERE aum = 'si' AND {permitido.FILTRO_SQL}
        )
        SELECT DISTINCT a.ticker
          FROM portafolio.tenencia t
          JOIN foto ON t.fecha = foto.f
          JOIN portafolio.assets a ON a.unidad = t.unidad
          JOIN mercado.curvas c ON c.ticker = a.ticker
         WHERE t.aum = 'si'
           AND {permitido.FILTRO_SQL}
           AND a.ticker IS NOT NULL
           AND c.fecha_vencimiento IS NULL
         ORDER BY a.ticker
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [c[0] for c in cur.description]
            filas = [dict(zip(cols, f, strict=True)) for f in cur.fetchall()]
            cur.execute(sql_sin, params)
            sin_vto = [f[0] for f in cur.fetchall()]
    except Exception as e:
        # El error vuelve como DATO, no como excepción: el ciclo se lo cuenta
        # al modelo y el modelo puede decir "no pude mirar" en vez de inventar.
        return {"error": f"no pude leer la cartera: {type(e).__name__}: {e}"}

    hoy = date.today()
    bonos = []
    foto_del = None
    for f in filas:
        foto_del = f.pop("foto_del", None)
        vto = f["fecha_vencimiento"]
        bonos.append({
            "ticker": f["ticker"],
            "vence": vto.isoformat() if vto else None,
            "dias_para_vencer": (vto - hoy).days if vto else None,
            "nominales": float(f["nominales"]) if f["nominales"] is not None else None,
            "valuacion": float(f["valuacion"]) if f["valuacion"] is not None else None,
            # ⚠️ Una moneda vacía NO se manda como null: se dice. En la
            # primera corrida real varias filas de tenencia vienen sin moneda,
            # y con un null el modelo simplemente omitía el dato — o sea que
            # mostraba un número de plata sin decir de qué moneda era.
            "moneda": f["moneda"] or "SIN DATO en la tenencia",
            "cuentas": int(f["cuentas"]),
        })

    return {
        "dias_mirados": n,
        # Va en la respuesta para que el modelo pueda decir de qué cuentas
        # habla. Sin esto, "te vencen 29 bonos" se lee como si fuera toda la
        # casa.
        "cuentas_miradas": permitido.cuentas(),
        "foto_del": foto_del.isoformat() if foto_del else None,
        "bonos": bonos,
        "cuantos": len(bonos),
        "sin_vencimiento_cargado": sin_vto,
    }


# ── LA FICHA QUE VE EL MODELO ───────────────────────────────────────────────

_TIPOS = {int: "integer", float: "number", str: "string", bool: "boolean"}


def ficha(fn) -> dict:
    """Convierte una función de Python en la ficha que se le manda al modelo.

    ── SU ROL EN EL CICLO: esto es lo ÚNICO que el modelo sabe de la herramienta ──

    Se arma con tres cosas que ya están en la función, así que no hay una
    segunda lista que mantener al día:

        el NOMBRE      → `fn.__name__`
        para QUÉ sirve → el docstring, tal cual
        qué ARGUMENTOS → la firma, con sus tipos

    Por eso «el docstring es el prompt» no es una metáfora: el texto que
    escribís arriba de la función es, palabra por palabra, lo que el modelo lee
    para decidir si la usa.
    """
    props, requeridos = {}, []
    for nombre, p in inspect.signature(fn).parameters.items():
        props[nombre] = {"type": _TIPOS.get(p.annotation, "string")}
        if p.default is inspect.Parameter.empty:
            requeridos.append(nombre)
    return {
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": inspect.getdoc(fn) or "",
            "parameters": {"type": "object", "properties": props,
                           "required": requeridos},
        },
    }


# Las herramientas disponibles. Sumar una es agregarla a esta lista y nada más:
# la ficha se arma sola desde la función.
DISPONIBLES = (bonos_que_vencen,)

# nombre → función, para que el ciclo pueda ejecutar lo que el modelo pidió.
POR_NOMBRE = {f.__name__: f for f in DISPONIBLES}

# La lista de fichas, lista para mandarle al modelo.
FICHAS = [ficha(f) for f in DISPONIBLES]
