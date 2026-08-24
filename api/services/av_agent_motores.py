"""api/services/av_agent_motores.py — SI UN MOTOR SE CAYÓ, EL AGENTE SE ENTERA.

Doc madre: **`docs/AV_AGENT.md`** §0.r.

Pedido del user (2026-08-19): *«con los logs de los motores lo mismo: quiero que
si hay alguno caído enterarme rápido (y a futuro que pueda hacer algo)»*.

**Todo el trabajo ya estaba hecho y el agente no lo miraba.**
`api/services/diagnostico_registry.py` tiene **50 piezas** —15 motores, 30 jobs,
5 APIs— cada una con su cadencia, su ventana horaria, su umbral de frescura y de
dónde se lee. `diagnostico.arbol()` las evalúa. Pero eso vivía SOLO en la pantalla
de Manager → OBSERVABILIDAD → DIAGNÓSTICO, o sea que había que ir a mirarla.

Este módulo no reimplementa nada: **lee el mismo árbol y convierte lo que está
mal en un hallazgo**, que es lo que hace que la señal te busque en vez de
esperarte. Es exactamente el mismo movimiento que se hizo con SALUD.

LA VENTANA ES LO QUE HACE QUE ESTO NO MIENTA
=============================================

Un motor fuera de rueda **no está caído: está apagado**, y los motores de mercado
los prende y los apaga el cron de lunes a viernes. Sin mirar la ventana, este
detector cantaría quince motores muertos todos los sábados — y en dos fines de
semana nadie volvería a leerlo.

`_en_ventana` ya resuelve eso en `diagnostico.py` (rueda 10:00-17:05 ART, agro
10:30, `always`, `diario`) y por eso las piezas fuera de ventana llegan acá con
estado `fuera_rueda`, que **no se reporta**.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Qué estados del árbol son un problema DE VERDAD. `lento` queda afuera a
# propósito: un motor que tarda el doble de su umbral sigue produciendo, y
# mezclarlo con uno muerto es cómo se pierde la diferencia entre las dos cosas.
_ROTOS = {"critico", "error", "sin_datos"}

# `fuera_rueda` y `sin_datos` fuera de ventana NO son problemas: son el sistema
# funcionando como tiene que funcionar.
_IGNORAR = {"fuera_rueda", "ok", "lento", "error_parse"}

# ── LA GRACIA DEL ARRANQUE ──────────────────────────────────────────────────
#
# ⚠️ **La ventana del árbol abre ANTES de que los motores arranquen**, y por eso
# hay un rato en que «no produjo» no significa «está caído» sino «todavía no
# prendió»:
#
#     10:00 ART   `_APERTURA["rueda"]` — la ventana del árbol abre
#     10:03-10:06 las piezas empiezan a dar CRÍTICO (umbral × 3, 60-120 s)
#     10:20 ART   los motores ARRANCAN de verdad (`20 13 * * 1-5` en el crontab)
#
# Son ~17 minutos de falsos positivos TODOS LOS DÍAS. En la pantalla de
# DIAGNÓSTICO eso ya pasaba y no molestaba (había que ir a mirarla); como
# hallazgo del agente sería un aviso en ALTA cada mañana, y **un detector que
# grita todos los días a la misma hora es un detector que se ignora** — el
# problema que este proyecto viene evitando en cada capa.
#
# La gracia se declara acá y no se toca `_APERTURA`: esa ventana la comparte la
# pantalla de DIAGNÓSTICO, y moverla para arreglar el detector cambiaría el
# estado de una vista que nadie pidió tocar.
#
# El número sale del crontab (arranque 13:20 UTC contra ventana 13:00) + un
# margen para que el motor se conecte y escriba lo primero. **No es un umbral de
# tolerancia**: pasado ese rato, un motor que no produce SÍ está caído y se canta.
GRACIA_ARRANQUE_MIN = 30


def detectar_motores() -> list[dict]:
    """Los motores, jobs y APIs que están rotos **ahora y en su ventana**."""
    try:
        from api.services import diagnostico
        arbol = diagnostico.arbol()
    except Exception as e:
        logger.warning("av_agent_motores: no pude leer el árbol: %s", e)
        return []

    if not arbol.get("en_rueda"):
        # Fuera de rueda solo se miran las piezas `always`/`diario`; el árbol ya
        # las devuelve con su estado real y las de rueda como `fuera_rueda`.
        logger.debug("av_agent_motores: fuera de rueda")

    if _recien_abrio(arbol):
        logger.debug("av_agent_motores: dentro de la gracia de arranque")
        return []

    ahora = _hora_del_arbol(arbol)
    out = []
    for vista in arbol.get("vistas") or []:
        for grupo in vista.get("grupos") or []:
            for p in grupo.get("piezas") or []:
                estado = (p.get("estado") or "").strip()
                if estado in _IGNORAR or estado not in _ROTOS:
                    continue
                # ⚠️ **`sin_datos` se decide ANTES de mirar la ventana.** En
                # `diagnostico._estado`, una pieza sin ningún dato devuelve
                # `sin_datos` sin pasar por `_en_ventana` — así que un motor de
                # mercado que nunca escribió salía en ALTA a las 3 de la mañana y
                # los sábados. El user lo marcó: *«es fundamental entender desde
                # qué hora hasta qué hora el error es real para cada motor»*.
                #
                # Se arregla ACÁ y no en `diagnostico`: ese estado lo comparte la
                # pantalla de DIAGNÓSTICO, y cambiarlo movería una vista que
                # nadie pidió tocar. `fuera_rueda` ya no llega hasta acá; esto
                # cubre el hueco que quedaba.
                if estado == "sin_datos" and not _en_su_ventana(p, ahora):
                    continue
                # ⚠️ **Y TAMPOCO SI TODAVÍA NO LE TOCÓ CORRER HOY.** `ventana`
                # dice cuándo importa la frescura; la CADENCIA dice desde qué
                # hora el cron puede haber producido algo. A las 11:13 un job
                # que arranca 12:00 no está atrasado — no le tocó. Ver
                # `_todavia_no_le_toco`.
                if _todavia_no_le_toco(p, ahora):
                    continue
                out.append(_hallazgo(vista.get("vista") or "?", p, estado, ahora))
    # Lo más grave primero, y dentro de eso los motores antes que los jobs: un
    # motor caído deja a la mesa sin precios AHORA; un job se recupera en la
    # corrida siguiente.
    out.sort(key=lambda h: (0 if h["severidad"] == "alta" else 1,
                            0 if h["evidencia"]["tipo"] == "motor" else 1))
    return out


def _recien_abrio(arbol: dict) -> bool:
    """¿Estamos en los primeros minutos de la rueda? (ver GRACIA_ARRANQUE_MIN).

    ⚠️ **La hora sale del ÁRBOL, no del reloj del proceso.** Eso ya lo decía este
    docstring y el código hacía otra cosa: llamaba a `ahora_ar()`. Son DOS
    relojes para juzgar UNA foto, y con eso el veredicto sobre el árbol podía no
    corresponder al momento en que el árbol se armó.

    Se descubrió por los tests: entre las 10:00 y las 10:30 ART, tres tests que
    pasan un árbol con hora fija fallaban — el detector les aplicaba la gracia de
    arranque leyendo el reloj de verdad. Fallaban media hora por día, o sea que
    en CI aparecían como un rojo intermitente sin causa aparente. Es el mismo bug
    que ya había en `salud._chequeo_job` y por el mismo motivo: cuando algo se
    evalúa contra una foto, el tiempo tiene que salir de la foto.
    """
    from datetime import time as _t

    from api.services.diagnostico import _APERTURA

    if not arbol.get("en_rueda"):
        return False
    ahora = _hora_del_arbol(arbol)
    if ahora is None:
        return False        # sin hora no se puede afirmar que esté en gracia
    abre: _t = _APERTURA["rueda"]
    minutos = (ahora.hour - abre.hour) * 60 + (ahora.minute - abre.minute)
    return 0 <= minutos < GRACIA_ARRANQUE_MIN


def _hora_del_arbol(arbol: dict):
    """La hora argentina que el árbol reporta. `None` si no la trae o no parsea.

    Devolver `None` y no el reloj del proceso es a propósito: caer al reloj
    silenciosamente sería volver a tener dos relojes, solo que a veces.
    """
    from datetime import datetime

    crudo = str(arbol.get("ahora_ar") or "").strip()
    for formato in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(crudo, formato)
        except ValueError:
            continue
    return None


# En palabras, porque el aviso lo lee una persona. `_APERTURA`/`_CIERRE` viven en
# `diagnostico` y se leen de ahí: escribir «10 a 17:05» a mano acá sería una
# segunda verdad que se desactualiza el día que muevan el horario (REGLA #9).
def ventana_en_palabras(ventana: str) -> str:
    from api.services.diagnostico import _APERTURA, _CIERRE

    abre = _APERTURA.get(ventana)
    if abre:
        return (f"corre de {abre.strftime('%H:%M')} a "
                f"{_CIERRE.strftime('%H:%M')} ART, de lunes a viernes")
    return {"always": "corre todo el día, todos los días",
            "diario": "corre una vez por día"}.get(ventana, "sin ventana declarada")


def _en_su_ventana(p: dict, ahora) -> bool:
    """¿La pieza debería estar produciendo AHORA?

    Sin hora del árbol devuelve True: *no poder saberlo no es lo mismo que estar
    fuera de horario*, y suprimir un aviso por una duda es peor que darlo.
    """
    if ahora is None:
        return True
    from api.services.diagnostico import _en_ventana
    return _en_ventana(ahora, p.get("ventana") or "rueda")


# Lo que pasó, en dos o tres palabras. El detalle va en el resto del motivo.
_QUE_PASO = {"critico": "sin producir", "error": "la corrida falló",
             "sin_datos": "nunca escribió"}


def _motivo(estado: str, p: dict, ahora) -> str:
    """`sin producir hace 40 min · esperado live · 14:22, en ventana`.

    Sin la VISTA (MERCADOS/PORTFOLIOS): el nombre de la pieza ya está en su
    columna y repetirlo gasta los caracteres que necesita la prueba.
    """
    partes = [_QUE_PASO.get(estado, estado)]
    hace = (p.get("hace") or "").strip()
    if hace and hace != "—" and estado != "sin_datos":
        partes[0] += f" {hace}"
    if p.get("cadencia"):
        # `cada 1m · 13-21 UTC L-V` → `cada 1m`: el horario ya va abajo.
        partes.append(f"esperado {str(p['cadencia']).split(' · ')[0]}")
    # ⚠️⚠️ **ESTO AFIRMABA «EN VENTANA» SIN CHEQUEAR NADA.** Era un string fijo
    # que se appendeaba a todos los motivos, siempre. El user lo cazó mirando
    # `pnl_totales_precompute`: el mensaje decía *«esperado 15-22 UTC · son las
    # 11:13, está en su ventana»* — y 11:13 ART son las 14:13 UTC, que NO está
    # en 15-22. Una afirmación falsa impresa con total seguridad.
    #
    # Peor: **tapaba la causa de fondo.** El texto y el chequeo salen de DOS
    # campos distintos de la misma pieza (`cadencia` es prosa, `ventana` es lo
    # que se evalúa) y estaban en desacuerdo. Mientras el mensaje mintiera igual,
    # ese desacuerdo no se podía ver. Ahora se pregunta de verdad, y cuando la
    # respuesta es que no, lo dice — que es cómo la discrepancia sale a la luz.
    if ahora is None:
        partes.append("sin hora")
    else:
        dentro = _en_su_ventana(p, ahora)
        partes.append(f"{ahora.strftime('%H:%M')}, "
                      + ("en ventana" if dentro else "FUERA de su ventana"))
    return " · ".join(partes)


# Cuánto se espera antes de volver a leer el log de la misma unidad. El monitor
# corre cada 5 minutos y esto es un subprocess por motor caído: sin freno, seis
# motores rotos son 72 lecturas por hora para mostrar la misma línea.
_LOG_CADA_S = 10 * 60
_ultimo_log: dict[str, tuple[float, str]] = {}


def _prueba_del_log(tipo: str, label: str) -> str:
    """La última línea que escribió ese motor. **La prueba de que falló.**

    *«Si tenés los logs de todo, o sea, es clarito»* (user). Un motor que no
    produce y cuyo log dice «WebSocket desconectado 13:48» ya no necesita que
    nadie investigue: el voto se puede emitir mirando la fila.

    Solo para MOTORES: un job no tiene unidad de systemd propia. Devuelve texto
    vacío si no se puede leer — media evidencia sirve, ninguna no.
    """
    if tipo != "motor":
        return ""
    unidad = label.split(" ")[0].strip()      # «motor_rofex (trades)» → unidad
    if not unidad.startswith("motor_"):
        return ""
    import time as _t
    cuando, previo = _ultimo_log.get(unidad, (0.0, None))
    if previo is not None and _t.monotonic() - cuando < _LOG_CADA_S:
        return previo
    try:
        from api.services import logs_sistema as ls
        r = ls.leer([unidad], desde="-6h", prioridad=7, lineas=1)
        lineas = r.get("lineas") or []
        if not r.get("disponible") or not lineas:
            texto = ""
        else:
            x = lineas[-1]
            from datetime import UTC, datetime

            from core.tz import AR_TZ
            # fromtimestamp SIN tz usaba la zona del server (UTC) y la
            # imprimía como si fuera local; y sin fecha, un log de ayer se
            # leía como de hoy (2026-08-22).
            hora = (datetime.fromtimestamp(x["ts"], UTC).astimezone(AR_TZ)
                    .strftime("%d/%m %H:%M"))
            texto = f"\nÚltimo log {hora}: {x['mensaje'].splitlines()[0][:110]}"
    except Exception as e:                                  # nunca hacia arriba
        logger.debug("av_agent_motores: sin log de %s (%s)", unidad, e)
        texto = ""
    _ultimo_log[unidad] = (_t.monotonic(), texto)
    return texto


# ⚠️⚠️ **UN JOB NO PUEDE LLEGAR TARDE ANTES DE QUE LE TOQUE** (2026-08-24).
#
# El user, a las 11:13 ART, con `pnl_totales_precompute` en ALTA:
#
#   *«están desconectados del tiempo y del espacio. Si escribe cada 60 minutos
#   y el mercado abre 10:30 es imposible que se rompa tan temprano. Y si encima
#   hace 2d como dice, fueron días no hábiles: tampoco es un error.»*
#
# Tenía razón y la causa es fina. Cada pieza declara DOS horarios que NO son lo
# mismo, y hasta hoy solo se miraba uno:
#
#     `ventana`   cuándo IMPORTA que el dato esté fresco → «rueda» (10:00-17:05 ART)
#     `cadencia`  cuándo el CRON efectivamente corre     → «15-22 UTC» = 12:00-19:00 ART
#
# `pnl_totales_precompute` corre desde las 12:00 ART. A las 11:13 estamos dentro
# de `rueda` pero **el job todavía no arrancó su día**: se lo estaba juzgando por
# no haber producido en una hora en la que ni siquiera está programado. No es un
# atraso, es que no le tocó.
#
# Y no alcanza con la hora de arranque: **hay que darle su propia cadencia de
# gracia**. Un job de 60 minutos que arranca a las 12:00 recién puede estar
# atrasado a las 13:00 — a las 12:13 no produjo nada porque faltan 47 minutos
# para su primera corrida, no porque esté roto. Es la misma idea que
# `GRACIA_ARRANQUE_MIN` para los motores, aplicada al job y con SU número.
#
# Esto NO reemplaza al umbral: solo impide que el reloj empiece a correr antes
# de que el job pudiera haber hecho algo.
_HORAS_UTC = __import__("re").compile(r"\b(\d{1,2})\s*-\s*\d{1,2}\s*UTC\b")


def _arranca_a_las(cadencia: str):
    """La hora ART a la que el CRON de esta pieza empieza su día, si la declara.

    Sale de la prosa de `cadencia` (`"cada 30m :05,:35 · 15-22 UTC L-V"`), que es
    donde vive el horario real del cron. `None` = no lo dice, y entonces no se
    puede afirmar nada: se deja pasar (no poder saberlo no es una excusa para
    suprimir un aviso).
    """
    from datetime import time as _t

    m = _HORAS_UTC.search(cadencia or "")
    if not m:
        return None
    utc = int(m.group(1))
    if not 0 <= utc <= 23:
        return None
    return _t((utc - 3) % 24, 0)      # ART = UTC-3, igual que el resto del repo


def _todavia_no_le_toco(p: dict, ahora) -> bool:
    """¿Estamos ANTES de la primera corrida posible de hoy (+ su cadencia)?

    Devuelve `False` ante cualquier duda: si la pieza no declara su horario, o
    no hay hora, o el arranque es posterior al cierre (un job nocturno), se
    prefiere avisar de más antes que callar un motor caído.
    """
    from datetime import datetime, timedelta

    if ahora is None:
        return False
    arranque = _arranca_a_las(str(p.get("cadencia") or ""))
    if arranque is None:
        return False
    inicio = datetime.combine(ahora.date(), arranque, tzinfo=ahora.tzinfo)
    if inicio > ahora:
        # El cron de hoy todavía no empezó: lo de ayer no es un atraso de hoy.
        # (Un job nocturno cuyo arranque «cae mañana» entra acá y se calla, que
        #  es lo correcto: su ventana es otra.)
        return True
    # Y una vez que arrancó, le corresponde UNA cadencia de gracia antes de que
    # se le pueda exigir el primer dato del día.
    gracia = timedelta(seconds=int(p.get("umbral_s") or 0))
    return ahora < inicio + gracia


def _explicacion(p: dict, ahora) -> str:
    """QUÉ le pasa a esta pieza, en castellano y sin números de máquina.

    Antes decía: *«Esperado: cada 30m :05,:35 · 15-22 UTC L-V · umbral 3600 s ·
    último dato 2d 15h · último run ok»*. El user: *«hay demasiado texto en cada
    aviso y no sirve, es todo muy mecánico»*. Y tenía razón — `umbral 3600 s` no
    le dice nada a nadie, y las dos líneas juntas no contestan la única pregunta
    que importa: **¿esto debería estar produciendo ahora mismo?**
    """
    partes = []
    # El campo `hace` viene a veces como «40 min» y a veces como «hace 40 min»
    # según quién lo formatee: se normaliza acá para no escribir «hace hace».
    hace = str(p.get("hace") or "").strip().removeprefix("hace").strip()
    partes.append(f"No produce desde hace {hace}." if hace and hace != "—"
                  else "Nunca produjo nada.")

    if ahora is not None:
        dentro = _en_su_ventana(p, ahora)
        partes.append(
            f"Son las {ahora.strftime('%H:%M')} y {'SÍ' if dentro else 'NO'} "
            f"es su horario: {ventana_en_palabras(p.get('ventana') or 'rueda')}."
            + ("" if dentro else " O sea que estar callado ahora es normal."))
    else:
        partes.append("No sé qué hora es para esta pieza, así que no puedo "
                      "decir si debería estar produciendo.")

    if (run := p.get("run_status")) and run != "ok":
        partes.append(f"Su última corrida terminó en «{run}».")
    return " ".join(partes)


def _hallazgo(vista: str, p: dict, estado: str, ahora=None) -> dict:
    tipo = p.get("tipo") or "pieza"
    label = str(p.get("label") or "?")
    # Un MOTOR caído es alta siempre: es el feed de precios de la mesa. Un job
    # crítico también; un `sin_datos` de una API externa es media — puede ser
    # que el proveedor esté caído y no nosotros.
    severidad = "alta" if (tipo == "motor" or estado == "critico") else "media"
    return {
        "tipo": "motor_caido", "ticker": label,
        "regla": {"critico": "sin_producir", "error": "fallo",
                  "sin_datos": "sin_datos"}.get(estado, estado),
        "severidad": severidad,
        # ⚠️ **LA PRUEBA VA EN EL MOTIVO, NO ADENTRO DEL MODAL.** El user
        # (2026-08-20): *«no le pone hora ni nada… si vas a decir eso, para
        # acertar me tenés que mostrar que falló en horarios donde debería
        # funcionar; si no, no tiene validez»*.
        #
        # Y tiene razón por un detalle de la pantalla que yo no había mirado:
        # **el botón ¿ACERTÓ? SÍ/NO está en la FILA**, y la fila muestra solo el
        # motivo. Toda la evidencia estaba una pantalla más abajo, así que se
        # pedía un voto sobre una frase sin datos — «hace rato que no produce»
        # no se puede votar. Un eval set alimentado así mide la paciencia del
        # que vota, no la puntería del agente.
        #
        # Entra: qué pasó · hace cuánto · qué se esperaba · la hora, y que está
        # DENTRO de su ventana. Corto igual (§0.ag).
        "motivo": _motivo(estado, p, ahora),
        "evidencia": {
            "texto": _explicacion(p, ahora) + _prueba_del_log(tipo, label),
            "tipo": tipo, "vista": vista, "estado": estado,
            "ventana": p.get("ventana"),
            "ventana_texto": ventana_en_palabras(p.get("ventana") or "rueda"),
            "ahora_ar": ahora.strftime("%H:%M") if ahora else None,
            "cadencia": p.get("cadencia"), "ultima": p.get("ultima"),
            "hace": p.get("hace"), "umbral_s": p.get("umbral_s"),
            "run_status": p.get("run_status")}}


def resumen() -> dict:
    """**¿Está la app funcionando bien AHORA?** — para el explicador.

    El user: *«no quiero que me muestre todos los endpoints; yo quiero saber que
    en horario de mercado la aplicación funciona bien y no hay nada
    colapsando»*. Esto devuelve el conteo, no la lista de 50 piezas.
    """
    try:
        from api.services import diagnostico
        arbol = diagnostico.arbol()
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    piezas = [p for v in (arbol.get("vistas") or [])
              for g in (v.get("grupos") or []) for p in (g.get("piezas") or [])]
    return {
        "ok": True,
        "en_rueda": bool(arbol.get("en_rueda")),
        "ahora_ar": arbol.get("ahora_ar"),
        "total": len(piezas),
        "bien": sum(1 for p in piezas if p.get("estado") == "ok"),
        "lentas": sum(1 for p in piezas if p.get("estado") == "lento"),
        "rotas": sum(1 for p in piezas if (p.get("estado") or "") in _ROTOS),
        "apagadas": sum(1 for p in piezas if p.get("estado") == "fuera_rueda"),
        "detalle_rotas": [
            {"label": p.get("label"), "tipo": p.get("tipo"),
             "estado": p.get("estado"), "hace": p.get("hace")}
            for p in piezas if (p.get("estado") or "") in _ROTOS],
    }


# ═══════════════════════════════════════════════════════════════════════════
# LO QUE DICEN LOS LOGS — calibrado con producción, no con umbrales inventados
# ═══════════════════════════════════════════════════════════════════════════
#
# `detectar_motores` (arriba) pregunta si el motor PRODUCE. Esto pregunta si el
# motor se está ROMPIENDO mientras produce — reconexiones, respuestas que no
# parsean, configuración vencida. Nada de eso llega a una tabla: vive en el log.
#
# ⚠️ **LOS NÚMEROS SALEN DE UNA MEDICIÓN REAL** (24 h, 14 motores, 2026-08-20).
# Elegirlos a ojo era la forma segura de que el detector gritara todos los días
# hasta que alguien lo silenciara. Lo que había:
#
#     ×76 en 3 min    motor_cedears     REST exception JSONDecodeError
#     ×91 en 6.7 h    motor_options     Expiries configuradas ya vencidas
#     ×1              motor_portfolio   ERROR símbolo inexistente, purgo y sigo
#     ×1 ×1 ×1        varios            warn sueltos, todos auto-resueltos
#
# Y ahí se ve que **la cuenta sola no alcanza**: 76 y 91 son parecidos y son dos
# problemas distintos. 76 en tres minutos es algo rompiéndose AHORA en loop; 91
# repartidas en siete horas es una configuración rota que nadie mira hace días.
# Por eso son dos reglas con nombres distintos y no un umbral con dos valores.
VENTANA_H = 24

# RÁFAGA: muchas repeticiones en poco tiempo. Con los datos, 30/15min deja pasar
# la de cedears (76 en 3') y NO la de options (91 en 6.7 h).
RAFAGA_VECES, RAFAGA_S = 30, 15 * 60
# MACHACA: se repite todo el día a ritmo lento. 20 deja pasar la de options y
# descarta los tres warn sueltos, que además eran auto-resueltos.
MACHACA_VECES = 20


def _dur(seg: float) -> str:
    if seg < 120:
        return f"{int(seg)} s"
    if seg < 7200:
        return f"{int(seg / 60)} min"
    return f"{seg / 3600:.1f} h"


def detectar_logs(*, horas: int = VENTANA_H) -> list[dict]:
    """Lo que los motores vienen diciendo y nadie lee.

    **Nunca levanta**: corre adentro del monitor de rueda y una excepción acá
    apagaría los otros detectores del mismo ciclo.
    """
    try:
        from api.services import logs_sistema as ls
        from api.services.diagnostico_registry import unidades_motores

        unidades = sorted(unidades_motores())
        r = ls.atencion(unidades, desde=f"-{horas}h")
    except Exception as e:
        logger.warning("av_agent_motores: no pude leer los logs: %s", e)
        return []

    if not r["disponible"]:
        # **«No pude leer» NO es «no hay errores».** Si esto devolviera lista
        # vacía, un host sin journal daría verde para siempre. Se canta como
        # hallazgo propio: el que mira tiene que saber que está mirando nada.
        return [{
            "tipo": "motor_ruidoso", "ticker": "logs", "regla": "no_pude_leer",
            "severidad": "media",
            "motivo": "No pude leer los logs de los motores",
            "evidencia": {"texto": (f"{r['motivo']}.\nNo es que estén bien: "
                                    "es que no sé cómo están."),
                          "motivo": r["motivo"]}}]

    out = []
    for g in ls.agrupar(r["lineas"]):
        h = _hallazgo_log(g)
        if h:
            out.append(h)
    out.sort(key=lambda h: (0 if h["severidad"] == "alta" else 1,
                            -h["evidencia"]["veces"]))
    return out


# El título entra en un renglón de la pantalla (mismo tope que
# `tests/unit/test_avisos_cortos.py`). Lo que se recorta es el patrón, porque el
# resto —cuántas veces, en cuánto, a qué hora— es lo que se vota.
TOPE_TITULO = 88


def _hora_de(ts: float) -> str:
    """La hora ARGENTINA de la última vez que apareció el patrón."""
    from datetime import UTC, datetime

    from core.tz import AR_TZ
    # Con FECHA: este sello viaja en motivos que se persisten (2026-08-22).
    return datetime.fromtimestamp(ts, UTC).astimezone(AR_TZ).strftime("%d/%m %H:%M")


def _titulo(unidad: str, patron: str, cola: str, hora: str) -> str:
    """`motor_options: Expiries ya vencidas · 91 veces en 6.7 h · 20:12`."""
    fijo = f"{unidad}: " + f" · {cola} · {hora}"
    hueco = TOPE_TITULO - len(fijo)
    pat = " ".join((patron or "").split())
    if hueco < 12:                       # unidad larguísima: el patrón no entra
        return f"{unidad}: {cola} · {hora}"[:TOPE_TITULO]
    if len(pat) > hueco:
        pat = pat[:hueco - 1].rstrip() + "…"
    return f"{unidad}: {pat} · {cola} · {hora}"


def _hallazgo_log(g: dict) -> dict | None:
    """Un patrón agrupado → hallazgo, o None si no llega a ser un problema.

    **Los warn sueltos se descartan a propósito.** En la medición eran tres, y
    los tres se anunciaban resolviéndose solos («reconectando (intento 1)»,
    «purgo y resuscribo sin ellos»). Reportar eso enseña a cerrar la pantalla
    sin leerla, y con ella se van los avisos que sí importan. El diag los sigue
    mostrando cuando alguien va a buscarlos.
    """
    veces, dur = g["veces"], max(0.0, g["ultima"] - g["primera"])
    es_error = g["peor"] <= 3

    # ⚠️ **LA FORMA CLASIFICA, EL NIVEL PESA.** La primera versión ponía el
    # `elif es_error` al final, así que un ERROR repetido 40 veces caía en
    # `machaca` y BAJABA a severidad media — el que más repite era el que menos
    # se veía. La forma dice qué está pasando; el nivel, cuánto importa.
    if veces >= RAFAGA_VECES and dur <= RAFAGA_S:
        regla, sev = "rafaga", "alta"
        cola = f"{veces} veces en {_dur(dur)}"
        detalle = "Falla y reintenta en loop."
    elif veces >= MACHACA_VECES:
        regla = "machaca"
        sev = "alta" if es_error else "media"
        cola = f"{veces} veces en {_dur(dur)}"
        detalle = "No es una caída: está mal hace rato y nadie lo mira."
    elif es_error:
        regla, sev = "error_de_motor", "media"
        cola = g["nivel"] + (f" ×{veces}" if veces > 1 else "")
        detalle = "Salió una vez. Si se repite sube solo de categoría."
    else:
        return None

    # ⚠️ **EL MOTIVO TIENE QUE DECIR CUÁL ES EL PROBLEMA Y CUÁNDO.** En la corrida
    # del 2026-08-20 `motor_options` salió DOS VECES con el mismo título
    # (`motor_options: 91 veces en 6.7 h`) y no había forma de saber que eran dos
    # patrones distintos: el que ve la lista cree que el agente repitió el aviso.
    # Y sin la hora no se puede votar —que es el pedido del user—: «96 veces en
    # 4 min» no dice si fue recién o a las 3 de la mañana.
    # ⚠️ **EL TÍTULO DICE QUÉ PASÓ, NO LA LÍNEA DEL LOG.** El user, mirando
    # `motor_curvas: <fecha>,<n> ERROR pg_mirror pg_mirror market_snapshot: de…`:
    # *«es imposible entender qué es el error, qué está pasando, si sigue
    # pasando. Poner una línea de código y decir que no anda es inentendible.»*
    #
    # Y tenía razón también en el porqué: **los motores hacen cosas LINEALES**.
    # Son piezas NUESTRAS y fallan de un conjunto finito de formas, así que casi
    # siempre se puede traducir con una regla, gratis y sin poder alucinar. Lo
    # que la regla no sabe lo traduce el modelo UNA vez por patrón (§0.ba).
    from api.services import av_agent_errores as trad
    exp = trad.explicar(g["unidad"], g["patron"], g.get("muestra") or "")
    motivo = _titulo(g["unidad"], exp["pasa"], cola, _hora_de(g["ultima"]))
    # Un WARNING repetido 90 veces por contratos vencidos es ruido; un ERROR de
    # escritura es plata que no se guardó. Lo que decide es la CONSECUENCIA, y
    # eso lo sabe la traducción — no el nivel con que se escribió el log.
    if not exp["urgente"] and sev == "alta":
        sev = "media"

    return {
        "tipo": "motor_ruidoso", "ticker": g["unidad"], "regla": regla,
        "severidad": sev, "motivo": motivo,
        "evidencia": {
            # QUÉ PASÓ · A QUÉ AFECTA · SI SIGUE. Las tres preguntas que el user
            # pidió poder contestar sin apretar nada, en ese orden.
            "pasa": exp["pasa"], "afecta": exp["afecta"],
            "sigue": cola, "fuente_texto": exp["fuente"],
            # Renglones SUELTOS, sin línea en blanco: es la ley del texto corto
            # (§0.ag) y hay un test que la exige. Un párrafo en un aviso se
            # saltea; tres renglones se leen.
            "texto": ("\n".join(x for x in (
                f"**{exp['pasa'].capitalize()}.** {exp.get('mas') or ''}".strip(),
                f"AFECTA: {exp['afecta']}." if exp["afecta"] else "",
                f"{detalle} {cola}.",
                f"Log: {(g.get('muestra') or '').splitlines()[0][:160]}",
            ) if x)),
            "unidad": g["unidad"], "nivel": g["nivel"], "veces": veces,
            "ventana_s": round(dur), "ventana": _dur(dur),
            "patron": g["patron"], "muestra": g["muestra"][:400]}}
