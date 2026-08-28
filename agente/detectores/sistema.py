"""Detectores de SISTEMA. Doc: `docs/AGENT_2.0.md` §5.

Todos devuelven `list[Hallazgo]` o levantan `SinDatos`. **Ninguno escribe.**

⚠️ **Ninguno lleva su propio `try/except`**, y eso es el cambio de fondo. En el
agente viejo cada uno de los 19 contestaba por su cuenta «¿qué hago si no puedo
mirar?»: unos devolvían vacío en silencio, otros emitían un hallazgo que lo
decía, y uno reventaba a propósito. Los tres son defendibles; el problema es que
era **la misma decisión tomada 19 veces**, y la vigésima se iba a tomar mal.

Acá el detector mira y devuelve, o levanta `SinDatos`. **Qué significa un fallo
lo decide el motor**, una sola vez, para todos.
"""
from __future__ import annotations

import logging

from agente import reloj
from agente.tipos import Hallazgo, SinDatos


def _humano(s: float) -> str:
    s = int(s or 0)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60} min"
    if s < 86400:
        return f"{s // 3600} h"
    return f"{s // 86400} d"

logger = logging.getLogger(__name__)


# ⚠️ **EL ESTADO, EN CASTELLANO Y EN UNA FRASE.** `critico`/`error`/`sin_datos`
# son etiquetas del árbol de diagnóstico, no una explicación: la tarjeta decía
# «tenencia (snapshot SQL): error» —el nombre repetido más una palabra— y ahí
# terminaba. Ninguna de las tres dice si hay que hacer algo.
_QUE_PASA = {
    "error": "su última corrida falló",
    "critico": "hace demasiado que no da señales",
    "sin_datos": "nunca dejó un rastro: puede no haber corrido jamás",
}


def _que_hacer_pieza(p: dict, dia: dict | None, nombre: str, unidad: str) -> str:
    """Qué hacer con una pieza rota — **distinto según qué es y qué se sabe.**

    La versión anterior era una sola frase para las 55 piezas: «relanzar X y
    mirar su log», y después, para todo lo que no tuviera botón, *«relanzar un
    motor en rueda le corta el feed de precios a la mesa»*. Esa segunda mitad es
    verdad para un motor y **mentira para un job**, que es la mayoría de las
    piezas: le explicaba al lector una regla de motores sobre un job.

    Lo que decide es otra cosa, y ahora se dice: **si el dato está o no**, y
    **si algo lo va a reintentar solo**. `run_job.sh` no reintenta ningún job:
    lo único que hay es su próxima corrida programada, y saberla es lo que
    separa «esperá» de «hacelo vos».
    """
    es_motor = (p.get("tipo") or "") == "motor"
    cad = p.get("cadencia") or "—"

    if es_motor:
        return (f"Mirar el log de `{unidad or nombre}`. Cadencia: {cad}. "
                "No hay botón a propósito: reiniciar un motor EN RUEDA le corta "
                "el feed de precios a la mesa, y eso lo decide la mesa.")

    if dia and dia["estado"] == "esta":
        return (f"Nada que rehacer: el día {dia['fecha']} ya está en "
                f"{dia['tabla']}. El job falló DESPUÉS de escribir, así que lo "
                "que hay que mirar es su log, no el dato.")

    if dia and dia["estado"] == "falta":
        return ("Apretá REHACER: relanza el job por el mismo lanzador del cron "
                f"y verifica mirando {dia['tabla']}. {dia['proximo']}.")

    if dia and dia["estado"] == "no_pude":
        return (f"No pude consultar {dia['tabla']}, así que no sé si hace falta "
                f"rehacer. Mirar la base a mano. {dia['proximo']}.")

    # No es relanzable: no hay botón porque no está declarado dónde se ve su
    # resultado — no porque sea peligroso. Decirlo así es lo honesto.
    return (f"Mirar el log de `{unidad or nombre}` y relanzarlo a mano. "
            f"Cadencia: {cad}. Todavía no tiene botón: para tenerlo hay que "
            "declarar en `agente/rehacer.py` en qué tabla se ve su resultado.")


def _renglon_del_dia(dia: dict | None) -> str:
    """Lo que se agrega al detalle según el veredicto sobre el DÍA.

    Los tres estados se atienden distinto y por eso se escriben distinto — que
    es el punto entero: **«el día está» y «no pude mirar» no se pueden dibujar
    igual**, ni entre ellos ni con «falta».
    """
    if not dia:
        return ""
    if dia["estado"] == "esta":
        return (f" · PERO EL DÍA {dia['fecha']} SÍ ESTÁ en {dia['tabla']}: "
                f"reventó después de escribir, no hay nada que rehacer")
    if dia["estado"] == "no_pude":
        return (f" · no pude consultar {dia['tabla']}, así que NO SÉ si el día "
                f"{dia['fecha']} está")
    return (f" · EL DÍA {dia['fecha']} NO ESTÁ en {dia['tabla']}"
            + (f" · {dia['rompe']}" if dia.get("rompe") else ""))


# ═══ salud ═════════════════════════════════════════════════════════════════
def salud(u: dict) -> list[Hallazgo]:
    """Jobs que no corrieron, corrieron con error, o dejaron el dato viejo.

    **No detecta: traduce.** El dueño de la evaluación es `api/services/salud`;
    acá se la lee y se convierte en hallazgos. Que sea un traductor está bien —
    lo que estaba mal es que declarara un arreglo sin tenerlo: su único botón
    vuelve a chequear, y **mirar no arregla** (§6.4). Por eso ahora es un AVISO.
    """
    from api.services import salud as motor
    try:
        chequeos = motor.evaluar()
    except Exception as e:
        raise SinDatos(f"el motor de salud no contestó: {e}") from e

    sev = {"error": "alta", "warn": "media"}
    out = []
    for c in chequeos or []:
        estado = (c.get("estado") or "").lower()
        if estado not in sev:
            continue                          # verde no es un hallazgo
        # El job que TERMINÓ BIEN pero anotó algo es otra cosa que el que falló:
        # se atiende distinto y por eso lleva su propia regla.
        regla = ("job_ok_con_avisos" if c.get("parcial")
                 else f"salud_{c.get('familia') or 'chequeo'}")
        out.append(Hallazgo(
            sujeto=str(c.get("id") or "?"), regla=regla, severidad=sev[estado],
            nombre=str(c.get("titulo") or c.get("id") or ""),
            problema=str(c.get("motivo") or "")[:900] or "no está en verde",
            # ⚠️ **EL ERROR DEL JOB, QUE YA VENÍA Y SE ENTERRABA.**
            # `salud._chequeo_job` guarda en `evidencia` el PRIMER error real de
            # la corrida (lo saca de `jobs_catalogo.ultimo.resumen`), y el agente
            # lo metía en un jsonb que la pantalla no lee. O sea: la fila decía
            # «la corrida falló» y el motivo estaba a un campo de distancia.
            detalle=("" if str(c.get("evidencia") or "") == str(c.get("motivo") or "")
                     else str(c.get("evidencia") or "")),
            que_hacer=(("No corrió después de su horario → mirar el scheduler. "
                        if c.get("corrio_despues") is False else
                        "Corrió y salió mal → el problema está en el código. ")
                       + f"Relanzar: `{c.get('id', '')}`."),
            evidencia={
                "chequeo_id": c.get("id"), "familia": c.get("familia"),
                "estado": estado, "n_casos": c.get("n"),
                "schedule": c.get("schedule"), "tabla": c.get("tabla"),
                "ultimo_at": c.get("ultimo_at"),
                "esperada_at": c.get("esperada_at"),
                # Separa las dos preguntas que se atienden distinto: si NO corrió
                # después de su horario el problema es el scheduler; si corrió y
                # salió mal, está adentro.
                "corrio_despues": c.get("corrio_despues"),
                "evidencia_salud": c.get("evidencia")}))
    return out


# ═══ motor_caido ═══════════════════════════════════════════════════════════
def motor_caido(u: dict) -> list[Hallazgo]:
    """Motores y jobs rotos **dentro de su ventana**.

    Fuera de rueda un motor no está caído: está apagado.

    ⚠️ **La hora sale del ÁRBOL, no del reloj del proceso.** Cuando algo se
    evalúa contra una foto, el tiempo tiene que salir de la foto — dos relojes
    juzgando la misma foto hacían fallar tres tests media hora por día.
    """
    from datetime import time as _t

    from agente import rehacer
    from agente.rehacer import cual_job
    from api.services import diagnostico
    try:
        arbol = diagnostico.arbol()
    except Exception as e:
        raise SinDatos(f"no pude leer el árbol de diagnóstico: {e}") from e

    ahora = _hora_del_arbol(arbol)
    if arbol.get("en_rueda") and ahora is not None:
        abre: _t = diagnostico._APERTURA["rueda"]
        mins = (ahora.hour - abre.hour) * 60 + (ahora.minute - abre.minute)
        if 0 <= mins < int(u.get("gracia_arranque_min", 30)):
            return []                     # arrancando no es caído

    rotos = {"critico", "error", "sin_datos"}
    ignorar = {"fuera_rueda", "ok", "lento", "error_parse"}
    out, ya_dichos = [], set()
    for vista in arbol.get("vistas") or []:
        for grupo in vista.get("grupos") or []:
            for p in grupo.get("piezas") or []:
                estado = (p.get("estado") or "").strip()
                if estado in ignorar or estado not in rotos:
                    continue
                # `sin_datos` se decide ANTES de mirar la ventana en el árbol,
                # así que un motor de mercado que nunca escribió salía en ALTA a
                # las 3 de la mañana. Se corrige acá y no allá: ese estado lo
                # comparte la pantalla de DIAGNÓSTICO.
                if estado == "sin_datos" and not _en_su_ventana(p, ahora):
                    continue
                if _todavia_no_le_toco(p, ahora):
                    continue
                sev = "alta" if estado in ("critico", "error") else "media"
                nombre = str(p.get("label") or p.get("unidad") or "?")
                unidad = str(p.get("unidad") or "")
                # ⚠️ **LA REGLA DICE SI HAY BOTÓN, Y EL BOTÓN SOLO APARECE
                # DONDE PUEDE HACER ALGO.** Primero se pregunta lo único que
                # decide —¿está el día en la tabla?— y de ahí sale la regla:
                #
                #   falta                → `job_sin_dato`, que TIENE botón.
                #   está / no pude mirar → `pieza_<estado>`, que es un aviso.
                #   no es relanzable     → `pieza_<estado>` (un motor, o un job
                #                          que todavía no declaró su tabla).
                #
                # Un botón sobre «el día ya está» solo podría contestar «no
                # hacía falta», y uno sobre «no pude consultar» solo «no ejecuto
                # a ciegas»: **un aviso con forma de trabajo**, que es lo que
                # hacía `salud` en el agente viejo y por lo que su lista de 96
                # filas no se podía apretar casi nunca.
                #
                # Y es una query por fila YA cantada, solo para los relanzables:
                # nunca un barrido de los 35 jobs cada 2 minutos.
                dia = _el_dia(unidad)
                regla = ("job_sin_dato" if dia and dia["estado"] == "falta"
                         else f"pieza_{estado}")
                # ⚠️ **EL NOMBRE CANÓNICO CUANDO SE SABE CUÁL ES.** El árbol
                # llama a este job «tenencia (snapshot SQL)» y `salud` le dice
                # `portafolio_diario`: dos tarjetas del MISMO incidente que el
                # lector no podía juntar. Y el sujeto ES la identidad del
                # problema, así que atarla al `unidad` del árbol la hacía
                # cambiar en silencio el día que alguien renombrara la Pieza.
                canon = (dia or {}).get("job") or cual_job(unidad)
                if canon:
                    ya_dichos.add(canon)
                out.append(Hallazgo(
                    sujeto=canon or unidad or nombre, regla=regla,
                    severidad=sev, nombre=canon or nombre,
                    # ⚠️ **SIN EL NOMBRE ADELANTE.** La tarjeta ya lo muestra
                    # grande arriba: repetirlo acá gastaba el renglón que tiene
                    # que decir QUÉ PASA.
                    problema=(f"{_QUE_PASA.get(estado, estado)} · última señal "
                              f"{p.get('hace') or '—'} · {reloj.hhmm()}"),
                    # ⚠️ **«ESCRITURA» NO: CORRIDA.** Cuando la frescura sale de
                    # `manager.job_runs` (las piezas con `run_tipo`, que son casi
                    # todos los jobs), ese timestamp es **cuándo corrió** — y un
                    # job que falló al arrancar «corrió» sin escribir una fila.
                    # La tarjeta decía «última escritura 08:00:04» de un job que
                    # no escribió nada.
                    detalle=(f"{'última escritura en ' + p['tabla'] if p.get('tabla') else 'última corrida'} "
                             f"{p.get('ultima') or 'NUNCA'} · tolera "
                             f"{_humano(p.get('umbral_s') or 0)}"
                             + _renglon_del_dia(dia)),
                    que_hacer=_que_hacer_pieza(p, dia, nombre, unidad),
                    evidencia={"vista": vista.get("vista"), "tipo": p.get("tipo"),
                               "estado": estado, "unidad": p.get("unidad"),
                               "tabla": p.get("tabla"),
                               "cadencia": p.get("cadencia"),
                               "ventana": p.get("ventana"),
                               "ultima": p.get("ultima"),
                               # Lo que `rehacer_job` necesita para saber a quién
                               # relanzar. Va en la evidencia y no se re-deduce.
                               "job": p.get("unidad"),
                               # `None` = no es relanzable, o no se pudo mirar.
                               # **No es `False`**: «no pude comprobar si el día
                               # está» jamás se publica como «el día falta».
                               "dia_faltante": (dia["fecha"] if dia
                                                and dia["estado"] == "falta"
                                                else None),
                               "dia_estado": (dia or {}).get("estado"),
                               "rompe": (dia or {}).get("rompe")}))
    # ⚠️⚠️ **EL DÍA QUE FALTA NO DEPENDE DE QUE ALGUIEN HAYA NOTADO EL FALLO.**
    #
    # Todo lo de arriba sale del árbol de diagnóstico, que juzga la pieza por su
    # último `run_status` y por la frescura. Ese veredicto PARPADEA: el 28/08 el
    # job del AuM falló a las 08:00, la tarjeta apareció, y para el mediodía
    # `motor_caido` tenía CERO abiertos mientras el día seguía sin escribirse
    # (medido: 0 filas y 0 en `portafolio.backfill_log` para el 27/08).
    #
    # O sea: **el botón colgaba de la señal que se apaga.** Y la pregunta que
    # decide no se apaga nunca — el día está en la tabla o no está—, así que se
    # hace igual, la haya visto el árbol o no. Es la misma lección que la de
    # `salud`: mirar el proceso no es mirar el resultado.
    #
    # No duplica: solo se agrega lo que el árbol NO cantó (`ya_dichos`).
    for job in rehacer.REHACIBLES:
        if job in ya_dichos:
            continue
        d = rehacer.estado_del_dia(job)
        if not d or d["estado"] != "falta":
            continue
        out.append(Hallazgo(
            sujeto=job, regla="job_sin_dato", severidad="alta", nombre=job,
            problema=(f"el día {d['fecha']} no está en {d['tabla']} y nadie lo "
                      f"va a escribir solo · {reloj.hhmm()}"),
            detalle=(f"{d['rompe']} · {d['proximo']}" if d.get("rompe")
                     else d["proximo"]),
            que_hacer=("Apretá REHACER: relanza el job por el mismo lanzador "
                       f"del cron y verifica mirando {d['tabla']}. Solo escribe "
                       "las cuentas que faltan: las que ya están no se tocan."),
            evidencia={"job": job, "dia_faltante": d["fecha"],
                       "dia_estado": d["estado"], "tabla": d["tabla"],
                       "rompe": d.get("rompe", "")}))

    # Lo más grave primero, y los motores antes que los jobs: un motor caído deja
    # a la mesa sin precios AHORA; un job se recupera en la corrida siguiente.
    out.sort(key=lambda h: (0 if h.severidad == "alta" else 1,
                            0 if h.evidencia.get("tipo") == "motor" else 1))
    return out


def _el_dia(unidad: str) -> dict | None:
    """El veredicto sobre el DÍA de este job, o `None` si no es relanzable.

    Delega en `rehacer.estado_del_dia`, que contesta con TRES estados distintos
    (`falta` · `esta` · `no_pude`). Antes esto vivía acá y devolvía `None` para
    los tres más «no es relanzable»: la tarjeta no podía distinguir «el dato
    está» de «no pude mirar», que es exactamente la diferencia entre quedarse
    tranquilo y tener que actuar.
    """
    from agente import rehacer
    return rehacer.estado_del_dia(unidad)


def _rehacible(unidad: str) -> bool:
    """¿Este job está declarado como relanzable?

    ⚠️ **La traducción del nombre NO se hace acá.** Un job se llama distinto en
    el cron, en el registro de diagnóstico, en `manager.job_runs` y en salud;
    `rehacer.cual_job` es el único que sabe cuál es cuál. Cuando esta función
    tenía su propia regla de sufijos, `jobs.portafolio_backfill` no resolvía a
    `portafolio_diario` y **el botón no aparecía nunca** — con el arreglo
    escrito, probado y andando del otro lado.
    """
    from agente.rehacer import cual_job
    return bool(cual_job(unidad))


def _hora_del_arbol(arbol: dict):
    from datetime import datetime

    from core.tz import AR_TZ
    # El árbol estampa `ahora_ar` como texto en hora ARGENTINA: se re-lee con
    # ese tz y no con `fromisoformat` pelado, que lo dejaría naive y compararlo
    # contra un aware LEVANTA — un monitor que se cae por un detalle de tipos
    # deja de avisar justo cuando hace falta.
    v = arbol.get("ahora_ar")
    if not v:
        return None
    try:
        return datetime.strptime(str(v), "%Y-%m-%d %H:%M:%S").replace(tzinfo=AR_TZ)
    except (TypeError, ValueError):
        return None


def _en_su_ventana(p: dict, ahora) -> bool:
    from api.services import diagnostico
    if ahora is None:
        return True
    return diagnostico._en_ventana(ahora, str(p.get("ventana") or "rueda"))


def _todavia_no_le_toco(p: dict, ahora) -> bool:
    """¿Estamos ANTES de la primera corrida posible de hoy?

    A las 11:13 un job que arranca 12:00 no está atrasado: no le tocó.

    ⚠️ **Hoy la hora de arranque sale de un REGEX sobre prosa** — cada pieza
    declara su cadencia como texto libre (`"cada 30m :05,:35 · 15-22 UTC L-V"`).
    Es la deuda que `AGENT_2.0.md` §5.1 deja anotada: el horario real vive en
    `deploy/crontab.txt` y en los units de systemd, que es la MISMA fuente que ya
    lee la habilidad `cron_desalineado`. Hasta entonces: ante cualquier duda
    devuelve `False` — avisar de más es mejor que callar un motor caído.
    """
    import re
    from datetime import datetime, timedelta
    from datetime import time as _t

    if ahora is None:
        return False
    m = re.search(r"(\d{1,2})\s*-\s*\d{1,2}\s*UTC", str(p.get("cadencia") or ""))
    if not m:
        return False
    utc = int(m.group(1))
    if not 0 <= utc <= 23:
        return False
    inicio = datetime.combine(ahora.date(), _t((utc - 3) % 24, 0),
                              tzinfo=ahora.tzinfo)
    if inicio > ahora:
        return True
    try:
        gracia = timedelta(seconds=float(p.get("umbral_s") or 0))
    except (TypeError, ValueError):
        gracia = timedelta(0)
    return ahora < inicio + gracia


# ═══ tabla_quieta ══════════════════════════════════════════════════════════
# Cada cuánto se rebarre el perfil de tablas, y a partir de cuándo se considera
# que ya no se puede mirar con él. Son DOS números y no uno: entre 20 y 48 horas
# el perfil está viejo pero todavía sirve —una tabla no cambia de ritmo en un
# día—; pasadas las 48 el universo es de otra época y seguir opinando sería
# exactamente la mentira que el invariante 1 prohíbe.
PERFIL_VENCE_H = 20
PERFIL_CIEGO_H = 48


def _perfil_vencido(tablas, *, tope_h: int = PERFIL_VENCE_H) -> bool:
    """¿Hace cuánto se midió el perfil? **Sin perfil = vencido**, no = al día:
    la primera corrida tiene que barrer, no asumir que ya está."""
    from datetime import UTC, datetime, timedelta
    filas = tablas.perfiles()
    medidos = [f["medido_at"] for f in filas if f.get("medido_at")]
    if not medidos:
        return True
    return max(medidos) < datetime.now(UTC) - timedelta(hours=tope_h)


def tabla_quieta(u: dict) -> list[Hallazgo]:
    """Tablas que dejaron de escribir cuando deberían estar escribiendo.

    **El ritmo se MIDE observando la tabla, no lo declara nadie**: cubre las ~190
    que son un punto ciego total. Las 8 con contrato declarado las sigue mirando
    SALUD, que es más estricto porque el aprendido se acostumbra al problema.
    """
    from agente import reloj, tablas
    from core import escribe

    # ⚠️⚠️ **EL PERFIL SE LO MANTIENE ESTA HABILIDAD.** `perfiles()` lee
    # `manager.tabla_perfil`: QUÉ tablas hay y CADA CUÁNTO escribe cada una. El
    # atraso se mide en vivo más abajo, pero **el universo y el ritmo salen de
    # ahí**, así que si nadie refresca ese perfil esta habilidad queda mirando
    # para siempre la foto del día que se sacó: una tabla nueva no entra nunca,
    # y una que cambió de ritmo se sigue juzgando con el viejo. Y reporta `ok`.
    #
    # Pasó de verdad: el barrido lo corría `jobs/db_tamano.py`, que se borró al
    # rehacer el agente (2026-08-24) sin que nadie tomara su lugar.
    #
    # **Por qué acá y no en un cron nuevo**: el agente tiene UN reloj. La memoria
    # es de la habilidad que la usa, igual que la foto de superficie en
    # `permiso_flojo`.
    #
    # **Por qué con guarda**: el barrido mide ~190 tablas, una query cada una.
    # Corre como mucho UNA vez por día y **fuera de rueda** — no puede robarle
    # tiempo a los motores, que es la REGLA #4. Los otros 47 pases del día leen
    # el perfil y no lo tocan.
    try:
        if _perfil_vencido(tablas) and not reloj.en_rueda():
            r = tablas.barrer()
            logger.info("tabla_quieta: perfil rebarrido — %s tablas, %s con ritmo",
                        r.get("tablas"), r.get("con_ritmo"))
    except Exception as e:
        # No se corta: un barrido que falla deja el perfil viejo, y con el perfil
        # viejo todavía se puede mirar. Lo que NO se puede es no enterarse.
        logger.warning("tabla_quieta: no pude rebarrer el perfil (%s)", e)

    try:
        con_contrato = tablas._ya_tienen_contrato()
        con_ritmo = tablas.perfiles(solo_con_ritmo=True)
        vivo = tablas._ultimo_dato_vivo(con_ritmo)
    except Exception as e:
        raise SinDatos(f"no pude leer el perfil de las tablas: {e}") from e

    # **Un perfil viejo NO se lee como un tablero limpio.** Si el barrido no pudo
    # correr y la foto quedó vieja de verdad, esta corrida no vio la base de hoy
    # y no puede cerrar nada por ausencia — el invariante 1, aplicado a su propia
    # memoria.
    if _perfil_vencido(tablas, tope_h=PERFIL_CIEGO_H):
        raise SinDatos(
            f"el perfil de tablas tiene más de {PERFIL_CIEGO_H} h y el barrido no "
            f"pudo refrescarlo: estaría juzgando la base de hoy con el universo "
            f"de otro día")

    # ⚠️ El ritmo DECLARADO en `deploy/crontab.txt`, resuelto UNA vez por
    # corrida: son ~190 tablas y leer el crontab por cada una sería el mismo
    # trabajo repetido. Le gana al medido — ver `tablas.frescura`.
    try:
        declarado = tablas.declarados()
    except Exception as e:
        logger.warning("tabla_quieta: sin ritmo declarado (%s)", e)
        declarado = {}

    out = []
    for i, p in enumerate(con_ritmo):
        if i in vivo:
            p = {**p, "ultimo_dato": vivo[i]}
        nombre = f"{p['schema']}.{p['tabla']}"
        if nombre in con_contrato:
            continue
        d = declarado.get(nombre)
        f = tablas.frescura(p, declarado=d)
        if f["estado"] != "atrasada":
            continue
        # ⚠️ **UNA TABLA DE EVENTOS NO TIENE CADENCIA: TIENE OCASIONES.** Está
        # quieta porque no pasó nada, no porque algo esté roto, y no hay nada
        # que relanzar. `no_se` NO se saltea: ante la duda se sigue exigiendo.
        if escribe.la_dispara(nombre) == escribe.EVENTO:
            continue
        out.append(Hallazgo(
            sujeto=nombre, regla="sin_escribir",
            severidad="alta" if p["cadencia"] == "tiempo_real" else "media",
            problema=str(f["motivo"]),
            # ⚠️ **DE DÓNDE SALE EL «DEBERÍA» — declarado o medido.** No es un
            # detalle: un ritmo medido puede estar equivocado (una ráfaga diaria
            # se lee como live) y uno declarado no. Quien lee la tarjeta tiene
            # que poder distinguirlos sin abrir el código.
            detalle=(f"{p['col_fecha']} = {f['ultimo_dato']} · "
                     + (f"su cron ({d['job']}) admite hasta "
                        f"{_humano(d['hueco_s'])} sin escribir" if d else
                        f"venía cada {_humano(p.get('intervalo_p50_s') or 0)} "
                        f"(medido, no declarado)")
                     + f" · {p['filas']:,} filas"),
            que_hacer=f"Relanzar {escribe.que_relanzar(nombre) or 'el job que la escribe'}.",
            evidencia={"cadencia": p["cadencia"], "col_fecha": p["col_fecha"],
                       "atraso_s": f["atraso_s"], "tope_s": f["tope_s"],
                       "ultimo_dato": f["ultimo_dato"], "filas": p["filas"],
                       # ¿El atraso se midió AHORA o salió de la foto del
                       # barrido? Sin esto, un veredicto viejo se lee igual que
                       # uno fresco.
                       "ultimo_vivo": i in vivo,
                       "la_escribe": escribe.quien_escribe(nombre),
                       "relanzar": escribe.que_relanzar(nombre)}))
    return out


# ═══ cron_desalineado ══════════════════════════════════════════════════════
#
# ⚠️ **UN HALLAZGO POR CRON, no uno por bolsa** (2026-08-28). Antes las dos
# reglas emitían UN hallazgo con `sujeto="crontab"` y el conteo en el texto, y
# los nombres de los jobs —que `crontab.que_job()` ya calculaba— se enterraban
# en `evidencia`, que AHORA no dibuja. La tarjeta terminaba diciendo tres veces
# lo mismo (la habilidad, el `nombre` a mano, el `problema`) y **ninguna decía
# CUÁL cron**.
#
# El texto era el síntoma; el defecto era el SUJETO. Con `sujeto="crontab"` los
# N crons comparten el trío `habilidad+sujeto+regla`, o sea comparten identidad:
#   · «no me interesa» sobre uno callaba a TODOS — y a los que aparecieran
#     después, que es exactamente lo que no se quiere silenciar;
#   · `veces` contaba vueltas de la bolsa, no de cada cron;
#   · arreglar uno no cerraba nada: solo bajaba el número.
def cron_desalineado(u: dict) -> list[Hallazgo]:
    """El crontab del repo contra el de la máquina, **en las dos direcciones**.

    `deploy.sh` no instala el crontab, así que un cron nuevo puede vivir en el
    repo y no ejecutarse nunca: no falla nada, el catálogo lo muestra igual, y el
    job no corrió.
    """
    from agente import crontab
    r = crontab.comparar()
    if not r.get("ok"):
        # **El silencio se lee igual que un verde.** Si la prueba no corrió hay
        # que decirlo — pero como AVISO, no cerrando nada.
        raise SinDatos("no pude leer el crontab de la máquina: no sé si los "
                       "crons del repo están instalados")

    # (regla, severidad, líneas, qué le pasa, qué hacer)
    lados = (
        ("sin_instalar", "alta", r["sin_instalar"],
         "está en `deploy/crontab.txt` y NO en la máquina: no se ejecuta",
         "Instalar el crontab del repo: "
         "`crontab /root/TradingAV/deploy/crontab.txt`."),
        ("sin_declarar", "media", r["sin_declarar"],
         "corre en la máquina y `deploy/crontab.txt` no lo declara",
         "Agregarlo a `deploy/crontab.txt` o sacarlo de la máquina: la próxima "
         "instalación se lo lleva puesto."),
    )
    out = []
    for regla, sev, lineas, pasa, hacer in lados:
        for linea in lineas:
            out.append(Hallazgo(
                sujeto=crontab.sujeto(linea), regla=regla, severidad=sev,
                # El NOMBRE es el job, sacado de la línea. Nunca escrito a mano:
                # si hay que redactar de qué es el problema, el sujeto está mal.
                nombre=crontab.que_job(linea),
                problema=f"{pasa} · {reloj.hhmm()}",
                # LA LÍNEA CRUDA. Es el único dato que dice qué corre y cuándo,
                # y hasta ahora vivía en un jsonb que ninguna pantalla lee.
                detalle=linea,
                que_hacer=hacer,
                evidencia={"job": crontab.que_job(linea), "linea": linea,
                           "lado": "repo" if regla == "sin_instalar" else "maquina"}))
    return out


# ═══ latencia ══════════════════════════════════════════════════════════════
def latencia(u: dict) -> list[Hallazgo]:
    """Endpoints degradados contra SU PROPIA normalidad, y los que dan 5xx.

    No es un ranking de lentos: un ranking muestra lo lento y esa lista no
    cambia nunca. La referencia es la mediana de las propias horas previas del
    endpoint.
    """
    from agente import latencia as maq
    try:
        casos = maq.comparar()
    except Exception as e:
        raise SinDatos(f"no pude leer la telemetría: {e}") from e

    out = []
    for c in casos:
        if c["roto"]:
            out.append(Hallazgo(
                sujeto=c["endpoint"], regla="errores", severidad="alta",
                problema=f"{c['errores']} de {c['n']} requests fallaron (5xx) "
                         f"en las últimas {maq.VENTANA_H} h",
                detalle=f"{c['errores']} de {c['n']} requests con 5xx en las "
                        f"últimas {maq.VENTANA_H} h",
                que_hacer="Mirar el log del endpoint: un 5xx está roto tarde lo "
                          "que tarde, no pasa por la comparación con su normal.",
                evidencia={"errores": c["errores"], "requests": c["n"]}))
        if c["degradado"]:
            out.append(Hallazgo(
                sujeto=c["endpoint"], regla="mas_lento",
                severidad="alta" if (c["veces"] or 0) >= 5 else "media",
                problema=f"tarda {c['veces']}× su normal · {c['avg_ms']} ms "
                         f"contra {c['base_ms']} ms (pico {c['max_ms']} ms)",
                que_hacer="Perfilar ese endpoint: la referencia es su propia "
                          f"mediana de las {maq.BASE_H} h previas.",
                evidencia={"avg_ms": c["avg_ms"], "base_ms": c["base_ms"],
                           "veces": c["veces"], "max_ms": c["max_ms"],
                           "requests": c["n"]}))
    return out


# ═══ proveedor_caido ═══════════════════════════════════════════════════════
def proveedor_caido(u: dict) -> list[Hallazgo]:
    """Proveedores de afuera que no responden: Aunesa, 1816, Interbanking, BCRA.

    Se entera por el **rastro de las llamadas REALES**, no por un health check
    que contesta bien mientras el endpoint que usamos devuelve 500. Y la prueba
    activa solo corre **cuando ya hay una falla**: nunca en el camino feliz,
    porque 1816 cobra por llamada.
    """
    from core.proveedores import PROVEEDORES, estado
    try:
        filas = estado()
    except Exception as e:
        raise SinDatos(f"no pude leer el estado de los proveedores: {e}") from e

    ahora = reloj.ahora_utc()
    ventana = float(u.get("ventana_s", 20 * 60))
    minimo = int(u.get("minimo_fallos", 1))
    out = []
    for f in filas:
        if f.get("ok") or not (cuando := f.get("ultimo_error_at")):
            continue
        if cuando.tzinfo is None:
            from datetime import UTC
            cuando = cuando.replace(tzinfo=UTC)
        if ((ahora - cuando).total_seconds() > ventana
                or (f.get("fallos_seguidos") or 0) < minimo):
            continue
        p = PROVEEDORES.get(f["proveedor"])
        nombre = p.nombre if p else str(f["proveedor"]).upper()
        veces = f.get("fallos_seguidos") or 1
        ok_at = f.get("ultimo_ok_at")
        desde = ""
        if ok_at is not None:
            from datetime import UTC
            o = ok_at.replace(tzinfo=UTC) if ok_at.tzinfo is None else ok_at
            mins = int((ahora - o).total_seconds() // 60)
            desde = (f"última respuesta buena hace {mins} min "
                     f"({reloj.hhmm(o)})")
        out.append(Hallazgo(
            sujeto=str(f["proveedor"]), regla="no_responde", severidad="alta",
            nombre=nombre,
            problema=f"{nombre} no responde · {veces} fallo(s) seguido(s) · "
                     f"{reloj.hhmm(ahora)}",
            # ⚠️ **EL ERROR CRUDO, SIN CORTAR NI TRADUCIR.** El código de error es
            # lo que dice de quién es el problema: un 401 es nuestro (se venció
            # una credencial), un 500 es de ellos, un timeout es la red. Antes
            # esto viajaba recortado a 120 caracteres adentro de una frase, con
            # un `que_hacer` de molde idéntico para los cuatro proveedores.
            detalle=str(f.get("ultimo_error") or "sin mensaje de error"),
            que_hacer=((f"Rompe: {p.rompe}. " if p and p.rompe else "")
                       + (desde + ". " if desde else "")
                       + "El código del error dice de quién es: 401/403 es una "
                         "credencial nuestra, 5xx es de ellos, timeout es la red."),
            evidencia={"fallos_seguidos": f.get("fallos_seguidos"),
                       "ultimo_error": f.get("ultimo_error"),
                       "ultimo_error_at": cuando.isoformat(),
                       "ultimo_ok_at": f.get("ultimo_ok_at"),
                       "rompe": p.rompe if p else ""}))
    return out


# ═══ db_peso ═══════════════════════════════════════════════════════════════
def db_peso(u: dict) -> list[Hallazgo]:
    """Tablas que crecieron fuera de lo suyo, **medido en vivo**.

    La medición y la serie viven en `agente/peso`: un detector mira y devuelve.
    El PESO de cada tabla no es un hallazgo —es información, y viaja aparte—;
    el hallazgo es lo que se movió.
    """
    from agente import peso

    try:
        hoy = peso.medir()
    except Exception as e:
        raise SinDatos(f"no pude medir el tamaño de la base: {e}") from e

    ayer = peso.de_hace(24)
    if not ayer:
        peso.guardar(hoy)
        # Sin referencia no se afirma nada. Es `[]` y no `SinDatos` porque la
        # medición SÍ corrió: simplemente todavía no hay contra qué comparar.
        return []

    total = sum(hoy.values()) or 1
    # El corte se auto-calibra sobre el tamaño real de la base: un umbral fijo
    # en MB envejece con la base y deja de significar nada.
    corte = max(int(total * 0.005), 20 * 1024 * 1024)
    out = []
    for tabla, b in hoy.items():
        prev = ayer.get(tabla)
        if prev is None or b <= prev or (b - prev) < corte:
            continue
        delta, pct = b - prev, round((b - prev) / prev * 100, 1) if prev else 100.0
        out.append(Hallazgo(
            sujeto=tabla, regla="crecio",
            severidad="alta" if pct >= 100 else "media",
            problema=f"creció {peso.mb(delta)} (+{pct}%) en 24 h · pasó de "
                     f"{peso.mb(prev)} a {peso.mb(b)} · {reloj.hhmm()}",
            que_hacer=f"Ver qué la está escribiendo. El corte para avisar es "
                      f"{peso.mb(corte)}, calculado sobre el tamaño real de la "
                      f"base — no un número fijo que envejece con ella.",
            evidencia={"bytes": b, "delta": delta, "pct": pct,
                       "corte_bytes": corte}))
    for tabla, b in ayer.items():
        if tabla not in hoy:
            out.append(Hallazgo(
                sujeto=tabla, regla="desaparecio", severidad="alta",
                problema=f"la tabla ya NO está · hace 24 h pesaba {peso.mb(b)}",
                que_hacer="Si fue a propósito, ignorala. Si no, alguien dropeó "
                          "algo.",
                evidencia={"bytes_antes": b}))
    peso.guardar(hoy)
    return out


# ═══ actividad ═════════════════════════════════════════════════════════════
def actividad(u: dict) -> list[Hallazgo]:
    """**LO QUE NO PUEDE PASAR EN UN DÍA NO HÁBIL.**

    Acá el razonamiento se invierte: no se mira si el dato está bien, se mira
    que NO HAYA dato nuevo. Sábado, domingo o feriado el mercado no abre, los
    motores están apagados por cron y nadie debería escribir precios.

    En día hábil devuelve `[]` **sin tocar la base**: el universo prohibido no
    existe.
    """
    from core.postgres import get_pool
    from core.tz import AR_TZ

    ahora = reloj.ahora_utc()
    if reloj.dia_habil(ahora):
        return []

    hoy_art = ahora.astimezone(AR_TZ).date()
    desde = reloj.arranco_el_dia(ahora)
    dia = {5: "sábado", 6: "domingo"}.get(hoy_art.weekday(), "feriado")
    out = []
    # ⚠️ Sin `try`: si la base no contesta, que levante. Tragarse el error acá
    # devolvería `[]`, la pasada quedaría como «ok» y cerraría hallazgos que
    # nadie volvió a mirar — la mentira optimista de siempre.
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*), max(updated_at) FROM mercado.market_snapshot "
                    "WHERE updated_at >= %s", (desde,))
        n, ultimo = cur.fetchone() or (0, None)
        if n:
            out.append(Hallazgo(
                sujeto="market_snapshot", regla="escribe_en_no_habil",
                severidad="alta",
                problema=f"hoy es {dia} y `mercado.market_snapshot` recibió {n} "
                         f"escrituras (última "
                         f"{ultimo.astimezone(AR_TZ).strftime('%H:%M') if ultimo else '—'})",
                que_hacer="Apagar el motor que quedó prendido o corregir el cron "
                          "que lo levanta.",
                evidencia={"escrituras": n, "dia": dia,
                           "ultima": ultimo.isoformat() if ultimo else None}))
        cur.execute("SELECT updated_at FROM operaciones.motor_heartbeat "
                    "WHERE id = 'current'")
        f = cur.fetchone()
        if f and f[0] and f[0] >= desde:
            out.append(Hallazgo(
                sujeto="motor_ordenes", regla="escribe_en_no_habil",
                severidad="alta",
                problema=f"hoy es {dia} y el heartbeat del motor de órdenes late "
                         f"({f[0].astimezone(AR_TZ).strftime('%H:%M')})",
                que_hacer="Apagar `motor_ordenes`: está corriendo con el mercado "
                          "cerrado.",
                evidencia={"latido": f[0].isoformat(), "dia": dia}))
    return out
