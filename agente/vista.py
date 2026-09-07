"""`agente/vista.py` — el READ MODEL. Doc: `AGENT.md` §6.

**Las pantallas LEEN. No derivan.** Clase, estado, arreglo y nombre vienen
resueltos de acá; ningún contador se suma en el navegador.

En el agente viejo el badge «AHORA 92» lo sumaba el browser juntando cuatro
cosas de DOS endpoints con frescuras distintas (uno se refrescaba cada 20 s, el
otro se cargaba una sola vez al abrir), contadas sobre listas ya cortadas en 200
filas, leídas de una tabla distinta de la que dibujaba LA LISTA. Nada de eso se
podía verificar del lado del servidor.

Acá cada pantalla es UNA query sobre UNA vista.
"""
from __future__ import annotations

import logging

from core.postgres import get_pool

logger = logging.getLogger(__name__)


def _filas(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _serializar(f: dict) -> dict:
    for k, v in list(f.items()):
        if hasattr(v, "isoformat"):
            f[k] = v.isoformat()
        elif isinstance(v, (int, float, str, bool, dict, list)) or v is None:
            pass
        else:
            f[k] = str(v)
    return f


# ── AHORA ──────────────────────────────────────────────────────────────────
def _con_historial(filas: list[dict]) -> list[dict]:
    """Le suma a cada hallazgo **cuántas veces apareció el mismo problema** en
    los últimos 30 días. Doc: `AGENT.md` §6.10.

    ⚠️⚠️ **ES LA PREGUNTA QUE DECIDE QUÉ HACER, y hasta hoy no se hacía.**

    El agente miraba cada hallazgo AISLADO, así que un job que no escribe se ve
    igual la primera vez que la trigésima — y las dos terminaban en «relanzá el
    job». Para la primera está bien. Para la trigésima, relanzar ES el parche:
    lo que hay que revisar es el umbral, el cron, o si el job sigue haciendo
    falta. El user lo dijo así: *«el agente debe poder buscar mejoras, no dejar
    todo como está y parchear»*.

    Un EPISODIO es una vez que el problema **nació**, no una vez que se lo vio:
    un problema que persiste no crea fila nueva (sube `veces`). Tres episodios
    son tres veces que apareció, se fue y volvió — que es justo lo que un
    incidente aislado NO hace.

    **UNA query para toda la lista**, no una por fila: AHORA puede traer
    cuarenta y cuarenta consultas para contestar lo mismo es cómo una pantalla
    se vuelve lenta sin que nadie sepa por qué. Va por el índice
    `hallazgos_problema (habilidad, sujeto, regla, detectado_at DESC)`, que ya
    existía.

    ⚠️ El trío viaja en TRES listas paralelas por `unnest`, no pegado en un
    string: un sujeto puede ser `mercado.market_snapshot` o `/api/x/{id}`, y
    cualquier separador es una apuesta a que no aparezca en los datos.

    Si la consulta falla, cada fila queda con `episodios = None` — **«no sé»,
    que no es lo mismo que «es la primera vez»**. Una pantalla que dice «1ª vez»
    porque no pudo contar es el invariante 1 disfrazado de dato.
    """
    from agente import tipos

    if not filas:
        return filas
    trios = {(f["habilidad"], f["sujeto"], f["regla"]) for f in filas}
    cuenta: dict[tuple, tuple] = {}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT d.hab, d.suj, d.reg, count(*)::int, min(h.detectado_at) "
                "  FROM unnest(%s::text[], %s::text[], %s::text[]) "
                "         AS d(hab, suj, reg) "
                "  JOIN agente.hallazgos h ON h.habilidad = d.hab "
                "   AND h.sujeto = d.suj AND h.regla = d.reg "
                " WHERE h.detectado_at > now() - make_interval(days => %s) "
                " GROUP BY d.hab, d.suj, d.reg",
                ([t[0] for t in trios], [t[1] for t in trios],
                 [t[2] for t in trios], tipos.VENTANA_CRONICO_D))
            cuenta = {(r[0], r[1], r[2]): (r[3], r[4]) for r in cur.fetchall()}
    except Exception as e:
        logger.warning("vista: no pude contar los episodios (%s) — las filas van "
                       "sin historial, que NO es «es la primera vez»", e)

    for f in filas:
        n, desde = cuenta.get((f["habilidad"], f["sujeto"], f["regla"]), (None, None))
        f["episodios"] = n
        f["episodios_desde"] = desde.isoformat() if desde is not None else None
        # `None` (no pude contar) NO es crónico: ante la duda, la rama que no
        # afirma nada.
        f["cronico"] = bool(n is not None and n >= tipos.EPISODIOS_CRONICO)
    return filas


def ahora() -> dict:
    """Lo de HOY, sin leer, sin resolver. **Un COUNT sobre tres condiciones.**

    El badge y la lista salen de la misma query, así que no pueden decir cosas
    distintas — que es lo que pasaba cuando el badge se sumaba en el navegador.

    AHORA **se vacía sola**: un hallazgo nace con la fecha del día en que se
    detectó, y al día siguiente sale aunque nadie lo haya leído. Funciona porque
    un problema que persiste NO crea fila nueva (sube `veces`), así que su
    `detectado_at` sigue siendo el del día que apareció.
    """
    filas = _con_historial([_serializar(f) for f in
                            _filas("SELECT * FROM agente.v_ahora")])
    return {"total": len(filas), "filas": filas}


def marcar_leidos(ids: list[int], *, por: str = "") -> dict:
    """«Ya me enteré». **Lo saca de AHORA y de NINGÚN otro lado.**

    LEÍDO ≠ RESUELTO: el hallazgo sigue abierto, sigue en ENCONTRÓ y sigue con
    su botón. Marcar leído baja el ruido del día, no cierra nada.

    Idempotente (`AND leido_at IS NULL`): marcar dos veces no pisa quién fue el
    primero.
    """
    ids = [int(i) for i in (ids or [])]
    if not ids:
        return {"ok": True, "marcados": 0}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE agente.hallazgos SET leido_at = now(), leido_por = %s "
                    "WHERE id = ANY(%s) AND leido_at IS NULL", (por, ids))
        return {"ok": True, "marcados": cur.rowcount or 0}


def desmarcar_leidos(ids: list[int]) -> dict:
    """Reversible desde la misma pantalla: leer no es una decisión importante y
    no tiene por qué ser irreversible."""
    ids = [int(i) for i in (ids or [])]
    if not ids:
        return {"ok": True, "vueltos": 0}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE agente.hallazgos SET leido_at = NULL, leido_por = '' "
                    "WHERE id = ANY(%s)", (ids,))
        return {"ok": True, "vueltos": cur.rowcount or 0}


# ── ENCONTRÓ ───────────────────────────────────────────────────────────────
def encontro() -> dict:
    """Lo abierto que TIENE ARREGLO.

    ⚠️ Un AVISO no entra acá. En el agente viejo `salud` declaraba una acción y
    caía en la lista de trabajo, pero su puerta era de solo lectura: lo único
    que ofrecía era «↻ chequear ahora». **Un aviso con forma de trabajo.**
    """
    from agente import arreglos as arr

    filas = _con_historial([_serializar(f) for f in
                            _filas("SELECT * FROM agente.v_encontro")])
    cat = {a["id"]: a for a in arr.catalogo()}
    for f in filas:
        f["arreglo_titulo"] = (cat.get(f["arreglo"]) or {}).get("titulo", "")
        f["arreglo_donde"] = (cat.get(f["arreglo"]) or {}).get("donde", "")
    por_habilidad: dict[str, int] = {}
    for f in filas:
        por_habilidad[f["habilidad"]] = por_habilidad.get(f["habilidad"], 0) + 1
    return {"total": len(filas), "filas": filas, "por_habilidad": por_habilidad}


def ignorar(hallazgo_id: int, *, por: str = "", motivo: str = "",
            deshacer: bool = False) -> dict:
    """«No me interesa». Reversible, y **no es un arreglo**: esconde, no resuelve.

    ⚠️⚠️ **SILENCIA EL PROBLEMA, NO LA FILA.** Marcar el hallazgo como
    `ignorado` —lo único que hacía antes— no alcanza y por eso el botón no
    servía: `registro._ver` busca por el TRÍO entre los ABIERTOS, un ignorado no
    está abierto, y en la pasada siguiente nacía una fila nueva en `nuevo`. El
    índice único tampoco chocaba, porque excluye `ignorado`. El bono descartado
    volvía a las dos horas como si fuera la primera vez.

    Van las DOS cosas y en este orden:

      1. la fila se marca `ignorado`, para que salga de la pantalla AHORA;
      2. el TRÍO entra en `agente.silenciados`, para que no vuelva a nacer.

    El botón vive en AHORA y en ENCONTRÓ (AGENT.md §0.dr: los avisos sin arreglo
    son la mayoría de AHORA y no tenían forma de callarse).

    Sin (2) el botón miente; sin (1) el aviso sigue en la lista hasta la próxima
    corrida. **Permanente por defecto** (`hasta` NULL): el vencimiento se carga a
    mano en la base cuando alguien lo elige, no lo pone el código.
    """
    from agente import tipos
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT habilidad, sujeto, regla FROM agente.hallazgos "
                    " WHERE id = %s", (hallazgo_id,))
        if not (f := cur.fetchone()):
            return {"ok": False, "error": "ese hallazgo no existe"}
        trio = (f[0], f[1], f[2])

        if deshacer:
            cur.execute("DELETE FROM agente.silenciados "
                        " WHERE habilidad = %s AND sujeto = %s AND regla = %s", trio)
            cur.execute("UPDATE agente.hallazgos SET estado = %s, cerrado_por = '' "
                        " WHERE id = %s AND estado = %s",
                        (tipos.NUEVO, hallazgo_id, tipos.IGNORADO))
            return {"ok": True, "silenciado": False}

        cur.execute(
            "INSERT INTO agente.silenciados (habilidad, sujeto, regla, por, motivo) "
            "VALUES (%s,%s,%s,%s,%s) "
            # Re-silenciar lo ya silenciado no es un error: actualiza quién y por
            # qué, y **renueva el «para siempre»** limpiando un vencimiento que
            # hubiera quedado de antes.
            "ON CONFLICT (habilidad, sujeto, regla) DO UPDATE SET "
            "  por = EXCLUDED.por, motivo = EXCLUDED.motivo, "
            "  desde = now(), hasta = NULL",
            (*trio, por, motivo))
        cur.execute("UPDATE agente.hallazgos SET estado = %s, cerrado_por = %s "
                    " WHERE id = %s AND estado = ANY(%s)",
                    (tipos.IGNORADO, por, hallazgo_id, list(tipos.ABIERTOS)))
        return {"ok": True, "silenciado": True,
                "habilidad": trio[0], "sujeto": trio[1], "regla": trio[2]}


# ── HISTORIAL ──────────────────────────────────────────────────────────────
LIMITE = 200


def historial(*, limite: int = LIMITE, desde_id: int | None = None,
              q: str = "", solo_malas: bool = False) -> dict:
    """El LIBRO, paginado **del backend**.

    En el agente viejo esto se armaba en el navegador juntando tres fuentes con
    topes distintos (100 acciones, 40 respuestas, 80 votos): cuando el más chico
    se agotaba, la línea de tiempo perdía un tipo de evento y no los otros, sin
    decirlo. Un día aparecía como que «solo tuvo acciones».

    Una fuente, un tope, declarado — y el filtro también es de acá: un buscador
    que solo mira lo que ya bajó no es un buscador.
    """
    limite = max(1, min(int(limite or LIMITE), 500))
    where, params = ["true"], []
    if desde_id:
        where.append("a.id < %s")
        params.append(int(desde_id))
    if solo_malas:
        where.append("a.ok = false")
    if (t := (q or "").strip()):
        where.append("(a.sujeto ILIKE %s OR a.arreglo ILIKE %s OR a.regla ILIKE %s "
                     " OR a.campo ILIKE %s OR a.donde ILIKE %s OR a.por ILIKE %s)")
        params += [f"%{t}%"] * 6

    # ⚠️⚠️ **EL ESTADO DEL HALLAZGO NO SIEMPRE HABLA DE ESTA LÍNEA.**
    #
    # User (2026-08-28), viendo nueve títulos que acababa de completar, cada uno
    # con su ✔ y su `emisor: — → OTROS`, y al lado «el aviso sigue abierto»:
    # *«¿por qué no pone CONFIRMADO? ¿qué tienen que ver los demás?»*.
    #
    # Y no tienen nada que ver. Cuando la acción es sobre UN título y el
    # hallazgo es de TODO el campo, el estado del hallazgo habla de los OTROS
    # —los que faltan— y en ese renglón se lee como una duda sobre la escritura
    # que la propia línea ya está afirmando.
    #
    # Se distingue con el dato, no con una lista: **si el sujeto de la acción es
    # distinto del sujeto del hallazgo, la acción es de un item y el hallazgo de
    # un grupo.** Ahí la línea contesta por sí sola y `estado_hoy` viaja vacío.
    filas = _filas(
        "SELECT a.*, h.severidad, "
        "       CASE WHEN h.id IS NULL OR h.sujeto IS DISTINCT FROM a.sujeto "
        "            THEN NULL ELSE h.estado END AS estado_hoy "
        "  FROM agente.acciones a "
        "  LEFT JOIN agente.hallazgos h ON h.id = a.hallazgo_id "
        f" WHERE {' AND '.join(where)} "
        " ORDER BY a.id DESC LIMIT %s", (*params, limite + 1))
    hay_mas = len(filas) > limite
    filas = [_serializar(f) for f in filas[:limite]]
    return {"filas": filas, "hay_mas": hay_mas,
            "ultimo_id": filas[-1]["id"] if filas else None,
            "limite": limite}


# ── LO CRÓNICO ─────────────────────────────────────────────────────────────
def cronicos(limite: int = 60) -> dict:
    """**Lo que pasa SIEMPRE — donde están las mejoras.** Doc: `AGENT.md` §6.10.

    Un problema que aparece treinta veces en un mes no es un incidente: es una
    configuración mal puesta, y arreglarlo cada vez lo TAPA. Esta lista es la
    única forma de verlo, porque mirando un hallazgo por vez los dos casos se
    ven idénticos.

    ⚠️ **LA CONSULTA VIVE ACÁ Y EN NINGÚN OTRO LADO.** `scripts/diag_agente` la
    LEE de esta función en vez de repetirla: dos definiciones de «crónico» —una
    para la pantalla y otra para la terminal— darían números distintos sobre el
    mismo problema sin que ninguna falle (REGLA #9). Ya pasó con el conteo de
    reincidencias, en tres lugares.

    Devuelve `activos` (pasó en los últimos `DIAS_ACTIVO`) y `historicos`
    aparte. **No es cosmético**: medido la primera vez que se listó, once de
    veinticinco ya no pasaban y competían por atención con los que rompen hoy.

    `mediana_s` es la MEDIANA de cuánto duró cada episodio, no el promedio: uno
    de cuatro horas entre cuarenta de tres minutos mueve el promedio a doce y
    cuenta una historia que no pasó.

    ⚠️ Y la duración mide **cuánto vivió el HALLAZGO**, no cuánto estuvo roto el
    mundo: tiene un piso puesto por la ventana del propio detector. En
    `proveedor_caido` (ventana de 20') una mediana de 20' significa «casi todos
    fueron un solo fallo» — que es justo lo que se quiere saber —, pero no se
    lee como «estuvo caído 20 minutos».
    """
    from agente import tipos

    sql = (
        "SELECT habilidad, sujeto, regla, count(*)::int AS episodios, "
        "       (percentile_cont(0.5) WITHIN GROUP ("
        "          ORDER BY extract(epoch FROM "
        "                   coalesce(cerrado_at, now()) - detectado_at)))::int AS mediana_s, "
        "       max(extract(epoch FROM "
        "           coalesce(cerrado_at, now()) - detectado_at))::int AS peor_s, "
        "       min(detectado_at) AS desde, max(detectado_at) AS ultima, "
        "       count(*) FILTER (WHERE estado = ANY(%s))::int AS abiertos, "
        "       max(detectado_at) > now() - make_interval(days => %s) AS activo "
        "  FROM agente.hallazgos "
        " WHERE detectado_at > now() - make_interval(days => %s) "
        " GROUP BY habilidad, sujeto, regla "
        "HAVING count(*) >= %s "
        " ORDER BY 10 DESC, 4 DESC LIMIT %s")
    filas = [_serializar(f) for f in _filas(
        sql, (list(tipos.ABIERTOS), tipos.DIAS_ACTIVO, tipos.VENTANA_CRONICO_D,
              tipos.EPISODIOS_CRONICO, int(limite)))]
    activos = [f for f in filas if f.get("activo")]
    return {
        "activos": activos,
        "historicos": [f for f in filas if not f.get("activo")],
        # El badge de la tab cuenta lo que SIGUE pasando: lo que ya se cortó no
        # es trabajo. Y sale de acá, no del navegador (invariante 11).
        "total": len(activos),
        "ventana_dias": tipos.VENTANA_CRONICO_D,
        "dias_activo": tipos.DIAS_ACTIVO,
        "desde_episodios": tipos.EPISODIOS_CRONICO,
    }


# ── LAS REINCIDENCIAS ──────────────────────────────────────────────────────
def reincidencias(limite: int = 100) -> dict:
    """**La que debe estar VACÍA.** Si tiene filas, algo que dimos por arreglado
    se rompió de nuevo. No es una lista de trabajo: es una alarma.

    ⚠️⚠️ **UNA REINCIDENCIA ESTÁ ACTIVA MIENTRAS SU HALLAZGO SIGA ABIERTO.**
    `agente.reincidencias` es una tabla de EVENTOS: sólo se le hace `INSERT` y
    no existe una línea que cierre una fila. Leerla entera hacía que la alarma
    **no pudiera volver a cero nunca**: M31G6 volvió el 28/08, el detector se
    corrigió ese mismo día, el hallazgo se cerró — y el cartel rojo siguió
    arriba de la pantalla una semana, describiendo un bono que ya venció.

    Eso es exactamente el defecto que el propio agente evita en el círculo del
    latido: *«un círculo que está en rojo cuando todo está bien enseña a ignorar
    el círculo»*. Una alarma que no puede apagarse deja de ser una alarma, y la
    fila número 16 —la que importaba— no la mira nadie.

    **El criterio NO es nuevo y no se inventa acá**: es el mismo `JOIN` que ya
    usa `lab.langgraph.cola.investigables()` para ofrecer casos. Estaba escrito
    en un solo lado y la pantalla usaba otro — la REGLA #9 adentro del agente.
    Ahora hay UNO.

    La fila **no se borra jamás**: «`alta_bono` aguantó 3,6 días sobre M31G6» es
    un hecho, y es la evidencia con la que después se decide qué arreglo es
    confiable. Sólo deja de contar como alarma. `historicas` dice cuántas hay
    apagadas, para que «desapareció» no se confunda con «lo borraron».
    """
    from agente import tipos

    filas = [_serializar(f) for f in _filas(
        "SELECT r.* FROM agente.reincidencias r "
        "  JOIN agente.hallazgos h ON h.id = r.hallazgo_id "
        " WHERE h.estado = ANY(%s) "
        " ORDER BY r.volvio_at DESC LIMIT %s",
        (list(tipos.ABIERTOS), int(limite)))]
    apagadas = _filas(
        "SELECT count(*) AS n FROM agente.reincidencias r "
        "  JOIN agente.hallazgos h ON h.id = r.hallazgo_id "
        " WHERE NOT (h.estado = ANY(%s))", (list(tipos.ABIERTOS),))
    return {"total": len(filas), "filas": filas,
            "historicas": int(apagadas[0]["n"]) if apagadas else 0}


# ── TODO EN UN REQUEST ─────────────────────────────────────────────────────
def vista() -> dict:
    """Todo lo que el modal necesita. **UN request.**

    Y con UNA sola noción de «ahora»: el agente viejo mezclaba un endpoint que
    se refrescaba cada 20 s con otro que se cargaba al abrir, y la pantalla
    sumaba los dos números como si hablaran del mismo instante.
    """
    from agente import catalogo, motor
    return {
        "ok": True,
        "latido": motor.vivo(),
        "ahora": ahora(),
        "encontro": encontro(),
        "reincidencias": reincidencias(20),
        # Va en el MISMO request que el resto: el modal se dibuja con UNA sola
        # noción de «ahora». Dos endpoints con frescuras distintas fue cómo el
        # agente viejo llegó a sumar números que hablaban de instantes distintos.
        "cronicos": cronicos(60),
        "habilidades": [_serializar(h) for h in catalogo.estado()],
    }
