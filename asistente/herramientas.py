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
from datetime import date, timedelta

from asistente import permitido
from core.postgres import get_pool

# Tope de cuántos días para adelante se puede preguntar. No es capricho: sin
# esto el modelo puede pedir 100.000 días y traerse el catálogo entero a la
# conversación, que se paga por token.
MAX_DIAS = 730


# ── CUÁNTA PLATA ENTRA, Y CUÁNDO ────────────────────────────────────────────
#
# ⚠️ **ESTA HERRAMIENTA NO CALCULA NADA, Y ESO ES LO QUE LA HACE CONFIABLE.**
#
# `operaciones.acreencias` ya tiene, por cuenta y por fecha de pago, cuánta
# plata cobra cada cliente y en qué moneda. La escribe `jobs/acreencias.py`
# (cron 12:45 UTC L-V, swap atómico) cruzando la última foto de tenencia contra
# el calendario contractual de cada bono, y ahí adentro ya están resueltas las
# tres cosas difíciles: el ajuste por CER, los bonos bullet (LECAP/BONCAP, que
# no tienen array de flujos y pagan todo al vencimiento) y la moneda de cada
# curva. La fórmula vive en `api/services/acreencias.py`.
#
# Rehacer esa cuenta acá sería la REGLA #9: dos versiones del mismo número sin
# árbitro, y el día que difieran ninguna de las dos falla — cada una contesta
# segura con su propio resultado.
#
# Techo de filas de detalle. Si se corta, se DICE (`truncado`): una lista
# recortada en silencio es un número mentiroso.
MAX_PAGOS = 300


def cuentas_disponibles() -> dict:
    """Las cuentas que podés consultar, con su nombre.

    ⚠️ **YA NO SE LE OFRECE AL MODELO COMO HERRAMIENTA**, y sigue acá a
    propósito: es de donde `ciclo._instruccion()` saca la lista que le pone
    delante en el SYSTEM. Con la lista arriba, el modelo no necesita pedirla —
    se ahorra una vuelta entera por conversación, y una ficha menos viaja en
    cada llamada.

    Devuelve el `id_cuenta` (que es lo que hay que pasarle a las herramientas) y
    la denominación del titular.
    """
    try:
        params = permitido.parametros()
    except permitido.SinPermiso:
        return permitido.como_error()

    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id_cuenta, denominacion FROM clientes.cuentas "
                " WHERE id_cuenta = ANY(%(cuentas_permitidas)s) ORDER BY id_cuenta",
                params)
            filas = {r[0]: r[1] for r in cur.fetchall()}
    except Exception as e:
        return {"error": f"no pude leer las cuentas: {type(e).__name__}: {e}"}

    # ⚠️ Se recorre la lista del PERMISO, no la de la base. Una cuenta
    # habilitada que no tenga fila en `clientes.cuentas` igual existe y se puede
    # consultar: omitirla acá la haría invisible para el modelo, que diría «no
    # tenés acceso a esa cuenta» sobre una cuenta a la que sí tiene acceso.
    return {
        "cuentas": [{"id_cuenta": c, "nombre": filas.get(c) or "(sin denominación cargada)"}
                    for c in permitido.cuentas()],
        "cuantas": len(permitido.cuentas()),
    }


def _por_mes(filas, mon) -> list[dict]:
    """Un renglón por mes con una clave por moneda. Las monedas NO se suman
    entre sí ni acá ni en ningún lado: son claves distintas del mismo renglón."""
    out: dict[str, dict] = {}
    for mes, moneda, monto in filas:
        out.setdefault(mes, {"mes": mes})[mon(moneda)] = round(float(monto or 0), 2)
    return [out[k] for k in sorted(out)]


def cobros_futuros(cuenta: str, dias: int) -> dict:
    """Cuánta PLATA va a cobrar una cuenta en los próximos N días, y en qué fechas.

    Es la única herramienta de plata que hay, y contesta las dos preguntas que
    parecen distintas y no lo son:

      · «¿cuánto cobro?» / «¿qué me pagan este mes?» / «¿cuánta plata entra?»
      · «¿qué bono me vence?» — un vencimiento es el ÚLTIMO cobro de un bono.
        Cada título trae su fecha de vencimiento en `vence`.

    ⚠️ `cuenta` es OBLIGATORIA: el `id_cuenta` sale de la lista que tenés en las
    instrucciones.

    QUÉ DEVUELVE:
      · `total` — la plata del período, SEPARADA POR MONEDA. ⚠️ NUNCA sumes
        pesos con dólares ni conviertas: dalos por separado, tal cual vienen.
      · `por_mes` — el mismo total abierto mes a mes. Usalo para decir dónde
        cae el grueso. NO lo calcules sumando `pagos`: ya viene hecho.
      · `titulos` — cuánto paga cada bono en total, y cuándo vence.
      · `pagos` — el detalle, un renglón por fecha y bono.
      · Todo lo que hay que sumar YA VIENE SUMADO. No rehagas las cuentas.

    AL CONTESTAR, aclarás siempre:
      · De qué cuenta hablás (viene en `cuenta`, con su nombre).
      · Sobre qué foto de cartera se proyectó (`tenencia_del`). Si es de hace
        varios días, decilo: una compra o una venta posterior no está adentro.
      · Si `truncado` es true, hubo más pagos de los que entraron en `pagos`.
        `total` y `titulos` siguen siendo del período COMPLETO.

    Los montos son el bruto contractual en la moneda del bono; los CER ya vienen
    ajustados por el último CER publicado. NO devuelve nominales ni valuación:
    esto es plata que entra, no cuánto vale la posición.

    Args:
        cuenta: el `id_cuenta` a mirar. Sale de `cuentas_disponibles`.
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

    # ⚠️ La cuenta la elige el MODELO, así que se valida contra el permiso. Si
    # inventa un número, acá se corta y se dice — no se contesta con otra
    # cuenta, que sería peor: una respuesta segura sobre lo que no se preguntó.
    pedida = str(cuenta or "").strip()
    if pedida not in permitido.cuentas():
        return {"error": f"la cuenta {pedida!r} no está habilitada para el asistente",
                "cuentas_habilitadas": permitido.cuentas(),
                "que_hacer": ("Preguntale al usuario cuál de las cuentas habilitadas "
                              "quiere. NO contestes con otra cuenta.")}
    params["cuentas_permitidas"] = [pedida]

    hoy = date.today()
    params["desde"] = hoy.isoformat()
    params["hasta"] = (hoy + timedelta(days=n)).isoformat()

    # ⚠️⚠️ **TODO SALE DE `operaciones.acreencias`, Y LA TENENCIA NO SE MIRA.**
    #
    # La tenencia ya se miró: la miró `jobs/acreencias.py` cuando escribió esta
    # tabla, para multiplicar los nominales por el cronograma del bono
    # (`api/services/acreencias.py:181`). Volver a mirarla acá sería rehacer esa
    # multiplicación — dos versiones del mismo número sin árbitro.
    #
    # `fecha_pago` es TEXT ISO, así que el >= / <= lexicográfico es el orden
    # cronológico (mismo criterio que `cashflow_sql`). La MONEDA va siempre en
    # el GROUP BY: sumar pesos con dólares da un número que parece bueno y no lo
    # es. `operaciones.acreencias` se aliasa `t` para que calce `FILTRO_SQL`.
    sql_pagos = f"""
        SELECT t.fecha_pago, t.ticker, t.moneda,
               sum(t.monto)           AS monto,
               max(t.data->>'emisor') AS emisor
          FROM operaciones.acreencias t
         WHERE {permitido.FILTRO_SQL}
           AND t.fecha_pago >= %(desde)s
           AND t.fecha_pago <= %(hasta)s
         GROUP BY t.fecha_pago, t.ticker, t.moneda
         ORDER BY t.fecha_pago, t.ticker
    """
    # El total por TÍTULO, con su vencimiento. Es la vista de arriba de la
    # pantalla de cobros futuros: qué bono paga cuánto en todo el período.
    #
    # `vence` sale del CATÁLOGO de renta fija (`mercado.curvas`), que es donde
    # vive la fecha de vencimiento de verdad — un `date`, no el texto libre de
    # `portafolio.assets.vencimiento`. Es un LEFT JOIN: un bono sin la fecha
    # cargada igual tiene que aparecer con su plata, que es lo que se preguntó.
    sql_titulos = f"""
        SELECT t.ticker, t.moneda,
               sum(t.monto)           AS total,
               max(t.data->>'emisor') AS emisor,
               max(c.fecha_vencimiento) AS vence
          FROM operaciones.acreencias t
          LEFT JOIN mercado.curvas c ON c.ticker = t.ticker
         WHERE {permitido.FILTRO_SQL}
           AND t.fecha_pago >= %(desde)s
           AND t.fecha_pago <= %(hasta)s
         GROUP BY t.ticker, t.moneda
         ORDER BY 3 DESC
    """
    # ⚠️⚠️ **ESTE AGREGADO LO ENCONTRÓ EL CONTROL DE NÚMEROS, EN SU PRIMERA
    # CORRIDA.** Sin `por_mes`, el modelo contestaba «septiembre 431,23 · octubre
    # 180,60» sumando los pagos a mano — bien, pero haciendo exactamente lo que
    # el prompt le prohíbe. Con seis pagos acierta; con sesenta, no hay razón
    # para creerle, y el error no se vería.
    #
    # La respuesta correcta a «el modelo está calculando» nunca es pedirle mejor
    # que no calcule: es darle el número hecho.
    sql_mes = f"""
        SELECT substr(t.fecha_pago, 1, 7) AS mes, t.moneda, sum(t.monto) AS monto
          FROM operaciones.acreencias t
         WHERE {permitido.FILTRO_SQL}
           AND t.fecha_pago >= %(desde)s
           AND t.fecha_pago <= %(hasta)s
         GROUP BY 1, t.moneda
         ORDER BY 1
    """
    # El total sale de su propia consulta sobre el período ENTERO, no de sumar
    # `pagos` — así el techo de filas del detalle no puede ensuciarlo. Si se
    # corta la lista, el total sigue siendo el de verdad.
    sql_total = f"""
        SELECT t.moneda, sum(t.monto) AS monto
          FROM operaciones.acreencias t
         WHERE {permitido.FILTRO_SQL}
           AND t.fecha_pago >= %(desde)s
           AND t.fecha_pago <= %(hasta)s
         GROUP BY t.moneda
    """
    # De cuándo es la foto de cartera, y quién es el titular. Los dos son de la
    # cuenta entera, así que no se filtran por fecha de pago.
    #
    # ⚠️ **NO viaja cuándo corrió el JOB** (`generado_at`), y se sacó a propósito:
    # el usuario pregunta AHORA, así que «calculado el …» no le dice nada que no
    # sepa, y el modelo lo repetía en cada respuesta. Lo que sí importa es de
    # cuándo es la FOTO: si el job quedó viejo, la foto también, así que
    # `snapshot` ya lo delata.
    sql_ficha = f"""
        SELECT max(t.data->>'snapshot'), max(t.data->>'cliente')
          FROM operaciones.acreencias t
         WHERE {permitido.FILTRO_SQL}
    """
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(sql_total, params)
            tot = cur.fetchall()
            cur.execute(sql_mes, params)
            meses = cur.fetchall()
            cur.execute(sql_titulos, params)
            tits = cur.fetchall()
            cur.execute(sql_pagos, params)
            filas = cur.fetchall()
            cur.execute(sql_ficha, params)
            snapshot, nombre = cur.fetchone()
    except Exception as e:
        # El error vuelve como DATO, no como excepción: el ciclo se lo cuenta al
        # modelo y el modelo puede decir "no pude mirar" en vez de inventar.
        return {"error": f"no pude leer los cobros: {type(e).__name__}: {e}"}

    def _mon(m) -> str:
        return (m or "SIN MONEDA").upper()

    return {
        "ventana": {"desde": params["desde"], "hasta": params["hasta"], "dias": n},
        "cuenta": {"id_cuenta": pedida, "nombre": nombre},
        "tenencia_del": snapshot,
        # ⚠️ Diccionario por moneda y NUNCA un número solo: si no existe un
        # total único, el modelo no puede reportar pesos sumados con dólares.
        "total": {_mon(m): round(float(v or 0), 2) for m, v in tot},
        # El mismo total abierto mes a mes, para que el modelo pueda decir dónde
        # cae el grueso SIN sumar.
        "por_mes": _por_mes(meses, _mon),
        "titulos": [{
            "ticker": t[0],
            "emisor": t[3],
            "vence": t[4].isoformat() if t[4] else None,
            "moneda": _mon(t[1]),
            "total": round(float(t[2] or 0), 2),
        } for t in tits],
        "pagos": [{
            "fecha": f[0],
            "dias": (date.fromisoformat(f[0]) - hoy).days,
            "ticker": f[1],
            "emisor": f[4],
            "moneda": _mon(f[2]),
            "monto": round(float(f[3] or 0), 2),
        } for f in filas[:MAX_PAGOS]],
        "cuantos_pagos": len(filas),
        "truncado": len(filas) > MAX_PAGOS,
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
# ⚠️ `cuentas_disponibles` NO está: su lista va en el SYSTEM
# (`ciclo._instruccion()`). Ofrecérsela además sería pagar su ficha en cada
# llamada y darle una opción más para elegir mal, por un dato que ya tiene.
DISPONIBLES = (cobros_futuros,)

# nombre → función, para que el ciclo pueda ejecutar lo que el modelo pidió.
POR_NOMBRE = {f.__name__: f for f in DISPONIBLES}

# La lista de fichas, lista para mandarle al modelo.
FICHAS = [ficha(f) for f in DISPONIBLES]
