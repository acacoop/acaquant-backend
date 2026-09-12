"""`agente/registro.py` — **LA ÚNICA PUERTA POR LA QUE EL AGENTE ESCRIBE.**

Doc: `docs/AGENT.md` §1.

El agente viejo tenía SEIS puertas escribiendo estado, cada una con su criterio
sobre qué guardar y qué significaba «resuelto». No era un mal diseño: era una
migración a medias. El costo no se paga en los bugs que ya salieron sino en que
**nada obligaba a una funcionalidad nueva a usar el pipeline que ya existía**.

Acá hay una función que escribe y un test que prohíbe el resto.

QUÉ HACE UNA CORRIDA, EN ORDEN
==============================

    1. sella la corrida en el CATÁLOGO — corra bien o mal, encuentre o no
    2. si el resultado NO es `ok`, TERMINA: no toca un solo hallazgo
    3. abre o refresca los hallazgos que vinieron
    4. cierra los que estaban abiertos y no vinieron — por CADUCIDAD si el
       sujeto dejó de existir (verificado), por ACCIÓN si había arreglo
       aplicado, por AUSENCIA si no
    5. anota las REINCIDENCIAS de los que ya se habían cerrado POR ACCIÓN
"""
from __future__ import annotations

import json
import logging

from agente import tipos
from core.postgres import get_pool

logger = logging.getLogger(__name__)


def sellar_corrida(habilidad: str, *, resultado: str, error: str = "",
                   duracion_ms: int = 0, traceback: str = "") -> None:
    """La fila del catálogo. **Va SIEMPRE**, y es lo que separa «corrí y no
    encontré nada» de «no corrí» — que en el agente viejo se veían iguales."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE agente.habilidades SET "
            "  ultima_corrida_at = now(), ultimo_resultado = %s, "
            "  ultimo_error = %s, ultimo_traceback = %s, ultima_duracion_ms = %s, "
            # El contador se resetea solo cuando cambia el día: sin la fecha al
            # lado, un contador miente en el primer cambio de día. Sin cron.
            "  corridas_hoy = CASE WHEN corridas_dia = current_date "
            "                      THEN corridas_hoy + 1 ELSE 1 END, "
            "  corridas_dia = current_date "
            "WHERE nombre = %s",
            (resultado, (error or "")[:500], (traceback or "")[:6000],
             int(duracion_ms), habilidad))


def guardar(habilidad: str, hallazgos, *, resultado: str = tipos.OK,
            error: str = "", duracion_ms: int = 0, traceback: str = "") -> dict:
    """Escribe lo que una corrida vio. **La única puerta.**

    ⚠️ **`resultado` es la guarda más importante del subsistema.** Solo una
    corrida `ok` puede cerrar por ausencia. Una que miró menos de lo habitual
    —la fuente no contestó, el detector levantó— no puede convertir su lista más
    corta en «se arreglaron 40 problemas»: es la mentira más cara que puede
    decir una herramienta de integridad, porque deja el tablero en verde justo
    el día que está más ciega.
    """
    todo = list(hallazgos or [])
    # ⚠️ **«NO LO PUDE MIRAR» VIAJA MEZCLADO CON LOS HALLAZGOS Y SE SEPARA ACÁ**
    # (`tipos.NoMirado`, §0.fa): el sujeto cuenta como VISTO para el cierre por
    # ausencia —lo que tuviera abierto sigue abierto— y como NADA para el
    # resto: no nace un hallazgo, no se le pone texto, no cuenta en AHORA.
    filas = [h for h in todo if not isinstance(h, tipos.NoMirado)]
    no_mirados = [h for h in todo if isinstance(h, tipos.NoMirado)]
    if no_mirados and resultado == tipos.OK and not error:
        # Va a `ultimo_error` con la corrida en `ok`: HABILIDADES lo muestra al
        # lado de «cuándo miró», que es donde se pregunta «¿y por qué no a
        # esta?». Los primeros tres con motivo, el resto contado.
        detalle = " · ".join(f"{n.sujeto} ({n.motivo})" if n.motivo else n.sujeto
                             for n in no_mirados[:3])
        resto = f" y {len(no_mirados) - 3} más" if len(no_mirados) > 3 else ""
        error = f"no pude mirar {len(no_mirados)}: {detalle}{resto}"
    # ⚠️ UNA sola vez por corrida: `sellar_corrida` suma `corridas_hoy`, y
    # sellar dos veces (lo cazó el revisor antes del push) contaba doble justo
    # en las pasadas con algo sin mirar.
    sellar_corrida(habilidad, resultado=resultado, error=error,
                   duracion_ms=duracion_ms, traceback=traceback)
    if resultado != tipos.OK:
        return {"ok": False, "resultado": resultado, "abiertos": 0,
                "nuevos": 0, "cerrados": 0, "reincidencias": 0,
                "silenciados": 0, "no_mirados": len(no_mirados)}

    nuevos = reincidencias = silenciados = 0
    vistos: list[tuple[str, str]] = [(n.sujeto, n.regla) for n in no_mirados]
    try:
        # UNA transacción: o entra todo lo que vio y se cierra lo que no vino, o
        # no entra nada. Media escritura es peor que ninguna — deja hallazgos
        # nuevos con los viejos sin cerrar, y nadie sabe de qué pasada es cada
        # cosa.
        with get_pool().connection() as conn:
            mudos = _silenciados(conn, habilidad)
            for h in filas:
                # ⚠️ **SE SALTEA LA FILA, NO LA MIRADA.** El detector ya lo vio,
                # así que entra igual a `vistos`: lo que se evita es CREARLE un
                # hallazgo, no dejar de mirarlo. Si no entrara acá,
                # `_cerrar_ausentes` lo daría por resuelto —el problema sigue
                # ahí— y al levantar el silencio reaparecería como nacido hoy,
                # con la antigüedad perdida.
                vistos.append((h.sujeto, h.regla))
                if (h.sujeto, h.regla) in mudos:
                    silenciados += 1
                    continue
                r = _ver(conn, habilidad, h)
                nuevos += 1 if r["nacio"] else 0
                reincidencias += 1 if r["reincidio"] else 0
            cerrados = _cerrar_ausentes(conn, habilidad, vistos)
    except Exception as e:
        # ⚠️ **LA CORRIDA NO PUEDE QUEDAR EN `ok` SI NO SE ESCRIBIÓ NADA.** El
        # sello va ANTES para que una caída dura igual deje rastro, pero si la
        # escritura falla hay que volver a sellar: si no, el catálogo dice
        # «miré y estaba todo bien» sobre una pasada que no guardó una fila.
        #
        # Pasó en la primera corrida real (2026-08-24): 12 habilidades
        # reventaron escribiendo y las 12 quedaron marcadas `ok`.
        logger.exception("agente/%s: no pude escribir lo que encontré", habilidad)
        sellar_corrida(habilidad, resultado=tipos.ERROR,
                       error=f"escribiendo: {type(e).__name__}: {e}"[:400],
                       duracion_ms=duracion_ms)
        raise
    return {"ok": True, "resultado": resultado, "abiertos": len(filas),
            "nuevos": nuevos, "cerrados": cerrados,
            "reincidencias": reincidencias, "no_mirados": len(no_mirados),
            # **Se cuenta, y se cuenta en la TERMINAL** (log y `diag_agente`), no
            # en el modal: el user pidió explícitamente que los silenciados no
            # ensucien la pantalla. Pero un silencio invisible del todo es cómo
            # muere un monitoreo — si una habilidad junta 40, esa habilidad está
            # mal pensada y el número tiene que poder decirlo.
            "silenciados": silenciados}


# ── EL TEXTO DE LA IA ──────────────────────────────────────────────────────
#
# Vive acá y no en `agente/redactar.py` por el invariante #5: **una función
# escribe hallazgos y un test prohíbe el resto**. El redactor es puro (arma el
# pedido, llama al gateway, valida) y no sabe que existe una base; la escritura
# entra por la misma puerta que todo lo demás.

def pendientes_de_texto(limite: int) -> list[dict]:
    """Los AVISOS abiertos que todavía no tienen texto del modelo.

    El filtro `arreglo = ''` es el alcance entero, y es DERIVADO: lo que tiene
    botón no se redacta porque su texto es el botón. No hay una lista de
    habilidades acá ni en ningún lado — una habilidad nueva sin arreglo entra
    sola, que es lo que pide la REGLA #10.
    """
    from agente import redactar
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT f.id, f.habilidad, f.sujeto, f.regla, f.problema, f.detalle, "
            "       f.que_hacer, f.evidencia, h.que_mira "
            "  FROM agente.hallazgos f "
            "  LEFT JOIN agente.habilidades h ON h.nombre = f.habilidad "
            " WHERE f.estado = ANY(%s) AND f.arreglo = '' "
            "   AND f.ia_texto = '' AND f.ia_intentos < %s "
            " ORDER BY f.detectado_at DESC LIMIT %s",
            (list(tipos.ABIERTOS), redactar.MAX_INTENTOS, int(limite)))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def guardar_texto_ia(hallazgo_id: int, *, texto: str, rechazo: str = "",
                     traza: int | None = None) -> None:
    """Escribe lo que redactó el modelo — **y NO toca `que_hacer`.**

    El texto determinista es el PISO: se conserva entero, así apagar la IA no
    deja un aviso mudo y se puede comparar una cosa con la otra. Tampoco toca
    `severidad`, `estado`, `arreglo` ni nada que DECIDA: el modelo explica, no
    resuelve.

    `ia_intentos` sube SIEMPRE, salga bien o mal. Es lo que impide que un
    hallazgo cuya evidencia no alcanza se pague en cada pasada del daemon para
    siempre: dos intentos y se queda con el piso.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE agente.hallazgos SET "
            "  ia_texto = %s, "
            "  ia_at = CASE WHEN %s <> '' THEN now() ELSE ia_at END, "
            "  ia_rechazo = %s, ia_llamada = COALESCE(%s, ia_llamada), "
            "  ia_intentos = ia_intentos + 1 "
            "WHERE id = %s",
            (texto, texto, (rechazo or "")[:200], traza, int(hallazgo_id)))


def borrar_textos_ia(habilidad: str = "") -> int:
    """Borra lo redactado de los avisos ABIERTOS para que se vuelva a escribir.

    **No es una limpieza de una vez: es la contracara de tocar el prompt o un
    validador.** Un texto se escribe UNA vez por hallazgo, así que un cambio en
    cómo se redacta no alcanza a lo que ya está en pantalla — y lo que está en
    pantalla es justamente lo que hizo falta cambiar. Sin esto, la única forma
    de aplicar una corrección era esperar a que el hallazgo cerrara y volviera.

    Tampoco toca `que_hacer`: lo que queda mientras se rehace es el piso.
    """
    donde = " AND habilidad = %s" if habilidad else ""
    args = (list(tipos.ABIERTOS), habilidad) if habilidad else (list(tipos.ABIERTOS),)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE agente.hallazgos SET ia_texto = '', ia_rechazo = '', "
            "  ia_at = NULL, ia_intentos = 0 "
            "WHERE estado = ANY(%s) AND arreglo = ''" + donde, args)
        return cur.rowcount


def _silenciados(conn, habilidad: str) -> set[tuple[str, str]]:
    """Lo que esta habilidad tiene silenciado, **de una sola query**.

    Se lee UNA vez por corrida y no una vez por hallazgo: una habilidad que ve
    200 sujetos no puede pagar 200 consultas para preguntar lo mismo.

    El vencimiento se filtra en SQL. `hasta IS NULL` = para siempre, que es el
    default: un silencio con fecha es una excepción que alguien cargó a mano.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT sujeto, regla FROM agente.silenciados "
            " WHERE habilidad = %s AND (hasta IS NULL OR hasta > now())",
            (habilidad,))
        return {(r[0], r[1]) for r in cur.fetchall()}


def _ver(conn, habilidad: str, h) -> dict:
    """Un hallazgo visto: lo abre si no estaba, o le suma una vuelta si estaba.

    El índice único parcial de `hallazgos` es el que garantiza que haya UNO
    abierto por problema. Es lo que reemplaza al «modo reemplazo» del agente
    viejo: sin él, un monitor de 30 segundos deja 2.880 filas del mismo problema
    por día.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE agente.hallazgos SET visto_ultima_vez = now(), "
            "  veces = veces + 1, "
            # La evidencia se REFRESCA: son los números de ahora, no los de la
            # primera vez. `detectado_at` NO se toca — es la fecha de nacimiento
            # y es lo que hace que AHORA se vacíe sola al día siguiente.
            "  evidencia = %s, problema = %s, detalle = %s, "
            "  severidad = %s, arreglo = %s "
            "WHERE habilidad = %s AND sujeto = %s AND regla = %s "
            "  AND estado = ANY(%s) RETURNING id",
            (json.dumps(h.evidencia or {}, default=str), h.problema,
             h.detalle, h.severidad, _arreglo_de(habilidad, h.regla), habilidad,
             h.sujeto, h.regla, list(tipos.ABIERTOS)))
        if (f := cur.fetchone()):
            return {"id": f[0], "nacio": False, "reincidio": False}

        # No estaba abierto. ¿Su último cierre fue POR ACCIÓN?
        #
        # ⚠️ **«EL ÚLTIMO», no «alguno»** (2026-09-04). Antes se pedía el más
        # reciente **entre los cerrados por acción**, saltéandose lo que hubiera
        # pasado después. Con la CADUCIDAD (§6.8) eso vuelve falsa su única
        # garantía: un bono arreglado en marzo, caducado en agosto porque
        # venció, y visto de nuevo en septiembre encontraba el cierre de marzo y
        # fabricaba una reincidencia — sobre un sujeto que el propio agente ya
        # había declarado muerto, con fuente y fecha.
        #
        # Se piden los dos cierres que AFIRMAN algo (`ausencia` sigue afuera,
        # como siempre: no dice nada) y gana el más nuevo. Si el más nuevo es
        # una caducidad, no hay reincidencia. Para acción/ausencia el
        # comportamiento no cambia en un solo caso.
        cur.execute(
            "SELECT id, cerrado_at, arreglo_aplicado, cerrado_como "
            "  FROM agente.hallazgos "
            " WHERE habilidad = %s AND sujeto = %s AND regla = %s "
            "   AND estado = %s AND cerrado_como = ANY(%s) "
            " ORDER BY cerrado_at DESC LIMIT 1",
            (habilidad, h.sujeto, h.regla, tipos.RESUELTO,
             [tipos.POR_ACCION, tipos.POR_CADUCIDAD]))
        previo = cur.fetchone()
        if previo is not None and previo[3] != tipos.POR_ACCION:
            previo = None

        # ⚠️⚠️ **REINCIDE EL ITEM, NO EL GRUPO (§0.cz).** Cuando el sujeto es un
        # CAMPO («CARTERA») y no un título, el trío de hoy y el de hace un mes
        # son el mismo problema aunque hablen de títulos distintos. Sin esto,
        # completar 4 títulos cerraba por acción, y el 5.º que entraba nuevo a
        # cartera «reincidía» — sobre un arreglo que nunca lo tocó. El user:
        # *«reincidencia sería que si yo agrego un emisor, ese bono vuelva a
        # estar sin emisor»*. Así que si el hallazgo trae sus `_items`, sólo hay
        # reincidencia cuando alguno de ELLOS fue escrito por la acción que
        # cerró el anterior. Sin `_items` (el sujeto ES el item) no cambia nada.
        # El `_` marca que es dato de MÁQUINA y la pantalla no lo dibuja.
        items = (h.evidencia or {}).get("_items")
        if previo is not None and isinstance(items, list):
            cur.execute(
                "SELECT DISTINCT sujeto FROM agente.acciones "
                " WHERE hallazgo_id = %s AND ok", (previo[0],))
            escritos = {r[0] for r in cur.fetchall()}
            if not escritos & {str(i) for i in items}:
                previo = None

        cur.execute(
            "INSERT INTO agente.hallazgos "
            " (habilidad, sujeto, regla, nombre, severidad, problema, detalle, "
            "  que_hacer, arreglo, evidencia, estado) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id, detectado_at",
            (habilidad, h.sujeto, h.regla, h.nombre or h.sujeto, h.severidad,
             h.problema, h.detalle, h.que_hacer, _arreglo_de(habilidad, h.regla),
             json.dumps(h.evidencia or {}, default=str), tipos.NUEVO))
        nid, nacido = cur.fetchone()

        if previo is None:
            return {"id": nid, "nacio": True, "reincidio": False}

        # ⚠️ **SOLO LO CERRADO POR ACCIÓN REINCIDE.** La guarda vive acá, en la
        # única función que inserta, porque la base no puede expresarla sin un
        # trigger. Si lo cerrado por AUSENCIA entrara, la tabla que debería
        # estar vacía se llenaría de bonos que no operaron esa noche.
        pid, cerrado_at, arreglo, _ = previo
        cur.execute(
            "INSERT INTO agente.reincidencias "
            " (hallazgo_id, hallazgo_previo_id, habilidad, sujeto, regla, "
            "  arreglo_aplicado, resuelto_at, volvio_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (nid, pid, habilidad, h.sujeto, h.regla, arreglo or "",
             cerrado_at, nacido))
        cur.execute("UPDATE agente.hallazgos SET estado = %s WHERE id = %s",
                    (tipos.REINCIDIO, nid))
        logger.warning("REINCIDIÓ %s/%s/%s — el arreglo «%s» no sirvió",
                       habilidad, h.sujeto, h.regla, arreglo or "?")
        return {"id": nid, "nacio": True, "reincidio": True}


def _cerrar_ausentes(conn, habilidad: str, vistos: list[tuple[str, str]]) -> int:
    """Lo que estaba abierto y esta corrida NO volvió a ver.

    Tres cierres, y el orden en que se prueban es el orden de cuánto afirman:

        CADUCIDAD  el sujeto dejó de existir, verificado y con fuente
        ACCIÓN     el arreglo se había aplicado (estaba `en_curso`)
        AUSENCIA   no lo vi, y nada más

    Esa diferencia es la que después decide si puede reincidir: sólo ACCIÓN
    puede (invariante 4). Ver `_caducados` para por qué la caducidad le gana a
    la acción y no al revés.
    """
    # ⚠️ **NADA DE PEGAR SUJETO Y REGLA EN UN STRING.** La primera versión los
    # unía con `chr(0)` y Postgres rechaza el NUL en un campo `text`: las 12
    # habilidades que corrieron en la primera pasada real murieron acá.
    #
    # Y el bug de fondo no era el byte elegido: **cualquier separador es una
    # apuesta a que no aparezca en los datos**. Un sujeto es un ticker, pero
    # también `mercado.market_snapshot` o `/api/x/{id}` — elegir un carácter
    # "imposible" es exactamente cómo nacen los bugs que no fallan, sino que
    # emparejan mal en silencio.
    #
    # Se comparan las DOS columnas por separado, con las dos listas en paralelo.
    # No hay separador, así que no hay nada que colisione.
    sujetos = [s for s, _ in vistos]
    reglas = [r for _, r in vistos]
    # El predicado «estaba abierto y esta corrida NO lo vio», UNA vez: lo usan
    # el SELECT que decide y el UPDATE que cierra. Dos copias de este `WHERE`
    # serían dos definiciones de «no vino» (REGLA #9), y la que se desincronice
    # no falla: cierra de más o de menos, callada.
    no_vino = ("habilidad = %s AND estado = ANY(%s) "
               "  AND NOT EXISTS (SELECT 1 FROM unnest(%s::text[], %s::text[]) "
               "                    AS v(s, r) "
               "                  WHERE v.s = sujeto AND v.r = regla)")
    params = (habilidad, list(tipos.ABIERTOS), sujetos, reglas)

    with conn.cursor() as cur:
        # `ORDER BY id`: con el tope de `vigencia`, cuáles caducan y cuáles
        # cierran por ausencia no puede depender del orden en que Postgres
        # devuelva las filas. Los más viejos primero.
        cur.execute("SELECT id, sujeto, regla, estado, arreglo_aplicado "
                    f"  FROM agente.hallazgos WHERE {no_vino} ORDER BY id",
                    params)
        se_van = cur.fetchall()
    if not se_van:
        return 0

    caducos = _caducados(conn, habilidad, se_van)
    with conn.cursor() as cur:
        if caducos:
            # ⚠️ **VA PRIMERO, y por eso no hace falta excluirlos abajo.** Al
            # cerrarlos dejan de estar en `ABIERTOS`, así que el UPDATE que
            # sigue —que filtra por `ABIERTOS`— ya no los alcanza. Una lista de
            # exclusión sería una tercera copia del criterio.
            cur.execute(
                "UPDATE agente.hallazgos SET "
                "  estado = %s, cerrado_at = now(), cerrado_como = %s, "
                "  cerrado_por = %s WHERE id = ANY(%s)",
                (tipos.RESUELTO, tipos.POR_CADUCIDAD, "agente/vigencia",
                 list(caducos)))
        cur.execute(
            "UPDATE agente.hallazgos SET "
            "  estado = %s, cerrado_at = now(), "
            "  cerrado_como = CASE WHEN estado = %s THEN %s ELSE %s END "
            f"WHERE {no_vino}",
            (tipos.RESUELTO, tipos.EN_CURSO, tipos.POR_ACCION,
             tipos.POR_AUSENCIA, *params))
        return (cur.rowcount or 0) + len(caducos)


def _caducados(conn, habilidad: str, se_van: list[tuple]) -> dict[int, object]:
    """De los que se van a cerrar, cuáles **dejaron de existir** — con el motivo.

    Devuelve `{id: Veredicto}` y deja una línea en el LIBRO por cada uno. No es
    un detalle: caducar es el agente escribiendo una decisión que nadie le pidió,
    y sin la línea sería lo mismo que un `except: pass` con mejor prensa.

    ⚠️ **Es un CIERRE, no un estado nuevo**, y le gana a los otros dos:

      · le gana a POR AUSENCIA porque dice más — «no lo vi» contra «venció el
        26/08 según `mercado.curvas`».
      · **le gana a POR ACCIÓN, y ese es el punto.** Si el arreglo se aplicó y
        DESPUÉS el sujeto se murió, no se le puede adjudicar el cierre a la
        acción; y sobre todo, cerrar por acción lo habilita a REINCIDIR. Un bono
        vencido que «vuelve» no significa nada, y así nació la primera fila de
        `agente.reincidencias` (M31G6: alta el 24/08, `cleanup_curvas` lo borró
        por vencer, de vuelta el 28/08). Lo que se aplicó no se pierde:
        `arreglo_aplicado` sigue en la fila y el libro tiene su línea.

    Nada de esto corre si la habilidad no declaró `sujeto_es`: sin declaración
    no hay caducidad, que es el default seguro.
    """
    from agente import catalogo, vigencia

    h = catalogo.HABILIDADES.get(habilidad)
    tipo = getattr(h, "sujeto_es", "") if h else ""
    if not tipo:
        return {}

    por_sujeto = vigencia.muertos(conn, tipo, [f[1] for f in se_van])
    if not por_sujeto:
        return {}

    caducos: dict[int, object] = {}
    for hid, sujeto, regla, estado, arreglo in se_van:
        v = por_sujeto.get(sujeto)
        if v is None:
            continue
        if len(caducos) >= vigencia.TOPE_POR_CORRIDA:
            # ⚠️ **NO SE CORTA LA CORRIDA: se cierra por la vía de siempre.**
            # Los que sobran caen en el UPDATE de ausencia, que no miente —
            # sólo dice menos. Frenar del todo dejaría hallazgos abiertos sobre
            # sujetos muertos para siempre; caducar 40 de una es más probable
            # que sea una fuente rota que 40 bonos venciendo el mismo día.
            logger.warning(
                "agente/%s: %d sujetos verificados como muertos supera el tope "
                "de %d por corrida — caducan %d y el resto cierra por ausencia. "
                "Si se repite, mirá la fuente antes que el agente",
                habilidad, len(por_sujeto), vigencia.TOPE_POR_CORRIDA,
                vigencia.TOPE_POR_CORRIDA)
            break
        caducos[hid] = v
        _anotar_caducidad(conn, habilidad=habilidad, sujeto=sujeto, regla=regla,
                          hallazgo_id=hid, estado=estado, arreglo=arreglo,
                          veredicto=v)
    return caducos


def _anotar_caducidad(conn, *, habilidad: str, sujeto: str, regla: str,
                      hallazgo_id: int, estado: str, arreglo: str,
                      veredicto) -> None:
    """La línea del LIBRO de una caducidad. **El fundamento Y la fuente.**

    Va con el MISMO `conn` que el cierre y no por `anotar_accion` (que abre el
    suyo): si la transacción de la corrida se cae, no puede quedar una línea
    diciendo que caducó algo que sigue abierto.

    `despues` lleva el motivo en castellano y `donde` la columna exacta de donde
    salió — «caducó» a secas no es trazabilidad, es un log. Lo que se lee en
    HISTORIAL queda: *«caducidad · M31G6 · estado: nuevo → resuelto · caducó:
    venció el 2026-08-26 · mercado.curvas.fecha_vencimiento»*.
    """
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agente.acciones (arreglo, habilidad, sujeto, regla, "
            " hallazgo_id, por, donde, campo, antes, despues, ok) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true)",
            ("caducidad", habilidad, sujeto, regla, hallazgo_id, "agente",
             veredicto.fuente[:200], "estado", estado[:200],
             f"resuelto · caducó: {veredicto.motivo}"[:200]))
    logger.info("agente/%s: CADUCÓ %s/%s — %s (%s)%s", habilidad, sujeto, regla,
                veredicto.motivo, veredicto.fuente,
                f" · tenía aplicado «{arreglo}»" if arreglo else "")


def _arreglo_de(habilidad: str, regla: str) -> str:
    from agente import catalogo
    h = catalogo.HABILIDADES.get(habilidad)
    return h.arreglo_de(regla) if h else ""


def anotar_accion(*, arreglo: str, habilidad: str, sujeto: str, regla: str,
                  hallazgo_id: int | None = None, por: str = "",
                  donde: str = "", campo: str = "", antes: str = "",
                  despues: str = "", ok: bool = True, error: str = "") -> None:
    """EL LIBRO. Lo que dibuja HISTORIAL.

    ⚠️ **El trío va SIEMPRE.** En el agente viejo la mayoría de las acciones no
    guardaban la regla que las motivó, y por eso la única columna que contestaba
    «¿quedó arreglado?» no podía contestarlo. Acá es obligatorio.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agente.acciones (arreglo, habilidad, sujeto, regla, "
            " hallazgo_id, por, donde, campo, antes, despues, ok, error) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (arreglo, habilidad, sujeto, regla, hallazgo_id, por, donde,
             campo, str(antes)[:200], str(despues)[:200], ok, (error or "")[:500]))
