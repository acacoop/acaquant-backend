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
        "motivo": (f"{tipo} de {vista}: "
                   + {"critico": "hace rato que no produce",
                      "error": "la última corrida falló",
                      "sin_datos": "nunca escribió nada"}.get(estado, estado)),
        "evidencia": {
            "texto": (f"{label} — cadencia esperada {p.get('cadencia') or '—'}, "
                      f"último dato {p.get('hace') or 'nunca'}"
                      + (f", último run {p.get('run_status')}"
                         if p.get("run_status") else "")
                      + f". El umbral de esta pieza es {p.get('umbral_s')} s.\n\n"
                      # DESDE Y HASTA QUÉ HORA el problema es real. Sin esto, el
                      # que lee no puede decidir si tiene que actuar ahora o si
                      # la pieza directamente no debería estar corriendo.
                      + f"CUÁNDO ES REAL: {ventana_en_palabras(p.get('ventana') or 'rueda')}"
                      + (f"; ahora son las {ahora.strftime('%H:%M')} ART y está "
                         "DENTRO de su ventana: no es que esté apagado."
                         if ahora else ". No pude leer la hora del árbol.")),
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
            "motivo": "no pude leer los logs de los motores",
            "evidencia": {"texto": (f"{r['motivo']}. Esto NO significa que los "
                                    "motores estén bien: significa que no sé "
                                    "cómo están."),
                          "motivo": r["motivo"]}}]

    out = []
    for g in ls.agrupar(r["lineas"]):
        h = _hallazgo_log(g)
        if h:
            out.append(h)
    out.sort(key=lambda h: (0 if h["severidad"] == "alta" else 1,
                            -h["evidencia"]["veces"]))
    return out


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
        motivo = f"{g['unidad']}: {veces} veces en {_dur(dur)}"
        detalle = ("Tantas repeticiones tan juntas no son ruido: es algo que "
                   "falla y se reintenta en loop.")
    elif veces >= MACHACA_VECES:
        regla = "machaca"
        sev = "alta" if es_error else "media"
        motivo = f"{g['unidad']}: {veces} veces en {_dur(dur)}"
        detalle = ("Repartido en el tiempo, así que no es una caída: es algo "
                   "que está mal desde hace rato y nadie lo mira.")
    elif es_error:
        regla, sev = "error_de_motor", "media"
        motivo = f"{g['unidad']}: {g['nivel']}" + (f" ×{veces}" if veces > 1 else "")
        detalle = ("Un error suelto puede ser un tropiezo. Queda anotado para "
                   "ver si vuelve: si empieza a repetirse, sube solo a "
                   "«machaca» o a «ráfaga».")
    else:
        return None

    return {
        "tipo": "motor_ruidoso", "ticker": g["unidad"], "regla": regla,
        "severidad": sev, "motivo": motivo,
        "evidencia": {
            "texto": (f"{detalle}\n\nPatrón: {g['patron']}\n\n"
                      f"Ejemplo: {g['muestra'].splitlines()[0][:200]}"),
            "unidad": g["unidad"], "nivel": g["nivel"], "veces": veces,
            "ventana_s": round(dur), "ventana": _dur(dur),
            "patron": g["patron"], "muestra": g["muestra"][:400]}}
