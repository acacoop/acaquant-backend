"""Detectores de SISTEMA. Doc: `docs/AGENT.md` §5.

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

    # No es relanzable: no corre por `run_job.sh` en `deploy/crontab.txt`, que
    # es de donde el agente saca el comando y la prueba (§0.db).
    return (f"Mirar el log de `{unidad or nombre}` y relanzarlo a mano. "
            f"Cadencia: {cad}. No tiene botón porque no está en el crontab por "
            "`run_job.sh`: lo que corre por ahí se rehace desde acá solo.")


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
                # Los PROCESOS los mira `motor_latido` por su latido (§0.da):
                # acá quedan los JOBS, que se juzgan por su resultado. Un
                # motor juzgado por la frescura de su tabla confundía «mercado
                # quieto» con «muerto», y dos habilidades sobre lo mismo son
                # dos relojes.
                if (p.get("tipo") or "") == "motor":
                    continue
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
    for job, cfg in rehacer.rehacibles().items():
        # Solo los que tienen prueba sobre el DATO (declarados o con contrato):
        # los de prueba «corrida» ya los canta el árbol por su run_status, y
        # consultarlos acá sería el barrido de 40 jobs cada 2 minutos.
        if job in ya_dichos or cfg.get("prueba") == rehacer.PRUEBA_CORRIDA:
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

    **El horario sale del crontab** (`rehacer.rehacibles()` → `schedule`) y lo
    evalúa el único evaluador cron del repo (`salud.ultima_ejecucion_esperada`).
    Hasta el 2026-09-02 salía de una expresión regular sobre la prosa de la
    cadencia (`"cada 30m · 15-22 UTC L-V"`), la deuda que §5.1 dejó anotada.
    Ante cualquier duda devuelve `False`: avisar de más es mejor que callar.
    """
    from datetime import UTC, timedelta

    from agente import rehacer

    if ahora is None:
        return False
    job = rehacer.cual_job(str(p.get("unidad") or ""))
    cfg = rehacer.rehacibles().get(job) if job else None
    if not cfg or not cfg.get("schedules"):
        return False
    ahora_utc = ahora.astimezone(UTC)
    esperada = rehacer.ultima_esperada(cfg, ahora_utc)
    if esperada is None:
        return False
    if esperada.date() < ahora_utc.date():
        return True                       # hoy todavía no disparó
    try:
        gracia = timedelta(seconds=float(p.get("umbral_s") or 0))
    except (TypeError, ValueError):
        gracia = timedelta(0)
    return ahora_utc < esperada + gracia


# ═══ motor_latido ══════════════════════════════════════════════════════════
def _edad_s(ts, ahora) -> float | None:
    if ts is None:
        return None
    try:
        return (ahora - ts).total_seconds()
    except TypeError:
        return None


def _iso_edad_s(iso: str | None, ahora) -> float | None:
    from datetime import datetime
    if not iso:
        return None
    try:
        return (ahora - datetime.fromisoformat(str(iso))).total_seconds()
    except (TypeError, ValueError):
        return None


def motor_latido(u: dict) -> list[Hallazgo]:
    """Cada proceso que systemd corre late solo, y acá se lee el latido.

    Cuatro veredictos, y la diferencia es lo que decide qué hacer:

      apagado   — systemd lo tiene inactive/failed dentro de su ventana.
      sin_latido — systemd dice active y nunca latió: corre código anterior al
                   latido (arrancó antes del deploy) o se colgó antes de latir.
      colgado   — latía y dejó de latir; systemd lo sigue viendo active.
      sin_feed / feed_mudo — vivo, pero el WS no está conectado, o lo está y
                   no llega un mensaje hace rato en plena rueda.

    **El universo sale de `deploy/systemd` + `deploy/crontab.txt`** (`agente/
    unidades.py`): un motor nuevo se espera desde que existe su unit. Y no hay
    botón a propósito: reiniciar en rueda le corta el feed a la mesa, y eso lo
    decide la mesa; el `que_hacer` trae el comando exacto.
    """
    from agente import fuentes, unidades

    decl = {k: v for k, v in unidades.declaradas().items() if v.get("proceso")}
    if not decl:
        raise SinDatos("no pude leer deploy/systemd: no sé qué procesos esperar")
    filas = fuentes.latidos()
    if filas is None:
        raise SinDatos("no pude leer operaciones.latidos")
    if not filas:
        raise SinDatos("ningún proceso late todavía: el latido entra con el "
                       "próximo arranque de los motores (cron 13:20 UTC)")

    ahora = reloj.ahora_utc()
    tol = float(u.get("tolerancia_s", 90))
    gracia = float(u.get("gracia_arranque_s", 120))
    mudo_s = float(u.get("feed_mudo_min", 10)) * 60
    estados = unidades.activas(sorted(decl))
    caliente = reloj.feed_caliente(ahora)

    out = []
    for unidad, d in sorted(decl.items()):
        v = d.get("ventana")
        if not unidades.en_ventana(v, ahora):
            continue
        desde = unidades.desde_inicio_s(v, ahora)
        if desde is not None and desde < gracia:
            continue
        proceso = d["proceso"]
        lat = filas.get(proceso)
        sysd = (estados or {}).get(unidad)
        sysd_txt = sysd or "no pude preguntarle a systemd"
        base_ev = {"unidad": unidad, "proceso": proceso, "systemd": sysd,
                   "ventana": v, "restart": d.get("restart")}
        reiniciar = (f"`systemctl restart {unidad}.service` — en rueda corta el feed "
                     "de la mesa, lo decide la mesa; fuera de rueda lo hace el cron.")

        if sysd in ("inactive", "failed", "deactivating"):
            out.append(Hallazgo(
                sujeto=unidad, regla="apagado", severidad="alta", nombre=unidad,
                problema=(f"systemd lo tiene «{sysd}» dentro de su ventana · "
                          f"{reloj.hhmm(ahora)}"),
                que_hacer=f"Arrancarlo: {reiniciar}",
                evidencia=base_ev))
            continue
        if lat is None:
            out.append(Hallazgo(
                sujeto=unidad, regla="sin_latido", severidad="alta", nombre=unidad,
                problema=(f"systemd dice «{sysd_txt}» y el proceso nunca latió · "
                          f"{reloj.hhmm(ahora)}"),
                detalle=("o corre código anterior al latido (arrancó antes del "
                         "deploy) o se colgó antes de latir por primera vez"),
                que_hacer=(f"Si es del deploy de hoy, se resuelve solo en el próximo "
                           f"arranque. Si no, {reiniciar}"),
                evidencia=base_ev))
            continue
        edad = _edad_s(lat.get("latido_at"), ahora)
        ev = {**base_ev, "pid": lat.get("pid"), "host": lat.get("host"),
              "arrancado_at": lat.get("arrancado_at"), "latido_at": lat.get("latido_at"),
              "latido_hace_s": None if edad is None else round(edad)}
        if edad is None or edad > tol:
            out.append(Hallazgo(
                sujeto=unidad, regla="colgado" if sysd == "active" else "muerto",
                severidad="alta", nombre=unidad,
                problema=(f"dejó de latir hace {_humano(edad or 0)} · systemd dice "
                          f"«{sysd_txt}» · {reloj.hhmm(ahora)}"),
                detalle=(f"último latido {lat.get('latido_at')} · pid {lat.get('pid')} "
                         f"· tolera {_humano(tol)}"),
                que_hacer=reiniciar,
                evidencia=ev))
            continue
        data = lat.get("data") or {}
        ws = data.get("ws")
        if ws is None:
            continue                      # no usa el WS: vivo alcanza
        ev.update({k: data.get(k) for k in ("ws", "ws_pedidos", "ws_mensajes",
                                            "ws_reconexiones", "ws_conectado_at",
                                            "ws_ultimo_mensaje_at", "ws_error")})
        if ws != "conectado":
            out.append(Hallazgo(
                sujeto=unidad, regla="sin_feed", severidad="alta", nombre=unidad,
                problema=(f"vivo pero el WebSocket está «{ws}» · "
                          f"{data.get('ws_reconexiones') or 0} reconexión/es · "
                          f"{reloj.hhmm(ahora)}"),
                detalle=str(data.get("ws_error") or ""),
                que_hacer=("Si «reconectando» dura minutos, el broker no vuelve: "
                           f"{reiniciar}"),
                evidencia=ev))
            continue
        mudo = _iso_edad_s(data.get("ws_ultimo_mensaje_at"), ahora)
        if caliente and (mudo is None or mudo > mudo_s):
            out.append(Hallazgo(
                sujeto=unidad, regla="feed_mudo", severidad="media", nombre=unidad,
                problema=(f"conectado y sin recibir un mensaje hace "
                          f"{_humano(mudo) if mudo is not None else 'nunca'} en plena "
                          f"rueda · {reloj.hhmm(ahora)}"),
                detalle=(f"{data.get('ws_pedidos') or 0} símbolos pedidos · "
                         f"{data.get('ws_mensajes') or 0} mensajes desde el arranque"),
                que_hacer=("Puede ser mercado quieto en lo que pide este motor. Si "
                           "los otros motores reciben y este no, " + reiniciar),
                evidencia=ev))
    return out


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

    # ⚠️⚠️ **SOLO NUESTRO TERRITORIO.** El inventario sale del catálogo de
    # Postgres, así que trae también los schemas que crea **Supabase** para sus
    # propios servicios: `auth`, `storage`, `realtime`, `vault`. Medido el
    # 2026-08-28: **33 tablas** que el agente venía juzgando sin saber de ellas
    # nada —ni quién las escribe ni cada cuánto deberían—. La primera que dio la
    # cara fue `realtime.schema_migrations`, marcada como «dejó de escribir»
    # estando perfecta: no está rota, no es nuestra.
    #
    # El filtro es por SCHEMA y no por tabla, a propósito: una tabla nueva en
    # `mercado` que todavía no esté en el archivo **se sigue mirando**. Lo que
    # queda afuera es el territorio ajeno, no lo que no llegamos a declarar.
    #
    # Y si no se puede leer el schema, `nuestros` viene vacío y **no se filtra
    # nada**: quedarse sin archivo no puede convertirse en dejar de mirar la
    # base entera.
    from agente import peso as _peso
    nuestros = _peso.schemas_nuestros()

    out = []
    for i, p in enumerate(con_ritmo):
        if i in vivo:
            p = {**p, "ultimo_dato": vivo[i]}
        if nuestros and p["schema"] not in nuestros:
            continue
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
                     # ⚠️ **CUÁNDO EL NÚMERO ES UNA COTA Y NO UNA MEDICIÓN.** Si
                     # la tabla no tiene sello de escritura, el atraso se midió
                     # contra una fecha de NEGOCIO (de qué día son los datos), y
                     # una fuente que entrega T-1 hábil arrastra hasta 3 días de
                     # desfase que no son un atraso. Decirlo es lo que hace el
                     # hallazgo votable (§0.ai); esconderlo es cómo salió el
                     # aviso falso de `mayor_movimientos` (§0.em).
                     + (" · ⚠️ es una fecha de negocio, no un sello de escritura: "
                        "el atraso es una COTA" if f.get("fecha_de_negocio") else "")
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


# ═══ foto_primary · foto_1816 ══════════════════════════════════════════════
def _cron_de(lineas: set[str], modulo: str) -> str | None:
    """La expresión cron (5 campos) del cron que corre `modulo`, leída del
    crontab del repo. **Se lee, no se copia**: si el horario viviera también acá,
    el día que alguien mueva el cron el detector juzgaría con el viejo (REGLA
    #9 B). La evalúa `salud.ultima_ejecucion_esperada`, el único evaluador cron."""
    for linea in lineas:
        if f"-m {modulo}" in linea:
            return " ".join(linea.split()[:5])
    return None


def _cron_discovery(lineas: set[str]) -> str | None:
    return _cron_de(lineas, "scripts.discovery_pyrofex")


def _foto(u: dict, *, modulo: str, fecha, sujeto: str, que: str,
          que_hacer: str) -> list[Hallazgo]:
    """UNA foto: una tabla que un cron refresca y que el resto del sistema lee
    como si fuera la verdad. Canta `nunca_corrio` o `foto_vieja` comparando la
    fecha de la foto contra la última corrida esperada del cron (§0.cy, §0.df).
    """
    from datetime import timedelta

    from agente import crontab
    from api.services import salud

    cron = _cron_de(crontab.del_repo(), modulo)
    if cron is None:
        raise SinDatos(f"no encuentro el cron de `{modulo}` en deploy/crontab.txt: "
                       "no sé cuándo debería refrescarse")
    ahora = reloj.ahora_utc()
    # La última corrida que YA debería haber terminado: la esperada a
    # (ahora − gracia), que descuenta lo que tarda en sacar la foto.
    esperada = salud.ultima_ejecucion_esperada(
        cron, ahora - timedelta(minutes=int(u.get("gracia_min", 60))))
    if esperada is None:
        raise SinDatos("no pude calcular la última corrida esperada")
    hora, minuto = esperada.hour, esperada.minute

    f = fecha()
    if f is None:
        return [Hallazgo(
            sujeto=sujeto, regla="nunca_corrio", severidad="alta",
            problema=f"nunca se sacó la foto de {que}: {sujeto} está vacía.",
            que_hacer=que_hacer,
            evidencia={"cron_utc": f"{hora:02d}:{minuto:02d}", "modulo": modulo})]
    if f >= esperada:
        return []
    atraso_h = (ahora - f).total_seconds() / 3600
    return [Hallazgo(
        sujeto=sujeto, regla="foto_vieja",
        severidad="alta" if atraso_h > 72 else "media",
        problema=(f"la foto de {que} es del {f:%d/%m %H:%M} UTC y el cron debió "
                  f"refrescarla el {esperada:%d/%m} a las {hora:02d}:{minuto:02d} UTC "
                  f"({_humano(atraso_h * 3600)} de atraso)."),
        que_hacer=que_hacer,
        detalle=f"generated_at={f.isoformat()} · esperada={esperada.isoformat()}",
        evidencia={"foto_de": f.isoformat(), "esperada": esperada.isoformat(),
                   "atraso_horas": round(atraso_h, 1),
                   "cron_utc": f"{hora:02d}:{minuto:02d}", "modulo": modulo})]


def foto_primary(u: dict) -> list[Hallazgo]:
    """La FOTO de Primary (`manager.pyrofex_instruments`) no quedó vieja.

    Es lo que filtra el WS de TODOS los motores, el alta del agente y
    `validar_instrumentos`. Hasta el 2026-09-01 la escribía un script manual y
    nadie: 17 días de foto, y S29E7 «inexistente» mientras OPERAR lo veía (§0.cy).
    """
    from agente import fuentes
    return _foto(u, modulo="scripts.discovery_pyrofex", fecha=fuentes.primary_fecha,
                 sujeto="manager.pyrofex_instruments", que="Primary",
                 que_hacer=("Correrlo ahora: `python -m scripts.discovery_pyrofex` (y "
                            "mirar logs/discovery_pyrofex.log si el cron no lo corrió). "
                            "Hasta entonces, todo bono nuevo es «inexistente» para el WS "
                            "y el alta."))


def foto_1816(u: dict) -> list[Hallazgo]:
    """El CATÁLOGO de 1816 (`research.mkt_1816_instrumentos`) no quedó viejo.

    De ahí leen `ficha_1816` (el emisor), `tamar_1816` (la grafía de las patas)
    y el alta del agente. Era manual hasta el 2026-09-02: S29E7 se licitó y no
    existía para ninguno de los tres (§0.df).
    """
    from agente import fuentes
    return _foto(u, modulo="jobs.mercado_1816_discovery",
                 fecha=fuentes.catalogo_1816_fecha,
                 sujeto="research.mkt_1816_instrumentos", que="el catálogo de 1816",
                 que_hacer=("Correrlo ahora: `python -m jobs.mercado_1816_discovery "
                            "--apply --catalogo` (~29 créditos) y mirar "
                            "logs/mercado_1816_discovery.log si el cron no lo corrió. "
                            "Hasta entonces, el emisor y la grafía TAMAR de un bono "
                            "nuevo no existen."))


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

    out = _vistas_ciegas(u)
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


def _vistas_ciegas(u: dict) -> list[Hallazgo]:
    """Lo que la mesa tiene enfrente y el servidor no ve (§0.dg).

    Cada pantalla que lleva más de un minuto sin poder refrescar deja un pulso
    (`agente.pulso_cliente`). Acá se agrupan por vista: cuántas personas,
    desde cuándo, qué pedido falla y con qué error. Y se cruza con el latido
    de la API: si arrancó adentro de la ventana, la causa más probable es el
    reinicio, y se dice. **No pudo leer ≠ no hubo pulsos**: sin tabla, no se
    afirma nada (SinDatos lo decide `latencia` entera).
    """
    from agente import fuentes

    ventana = int(u.get("pulso_ventana_min", 10))
    filas = fuentes.pulsos(ventana)
    if filas is None:
        raise SinDatos("no pude leer agente.pulso_cliente")
    if not filas:
        return []
    ahora = reloj.ahora_utc()
    api = (fuentes.latidos() or {}).get("api.main") or {}
    arranco = api.get("arrancado_at")
    reinicio = (arranco is not None
                and (ahora - arranco).total_seconds() <= ventana * 60)

    por_vista: dict[str, list[dict]] = {}
    for f in filas:
        por_vista.setdefault(f["vista"], []).append(f)
    out = []
    for vista, ps in sorted(por_vista.items()):
        personas = {p["email"] for p in ps if p["email"]}
        desde = min((p["desde_at"] for p in ps if p["desde_at"]), default=ps[0]["at"])
        ultimo = max(p["at"] for p in ps)
        minutos = max(1, round((ultimo - desde).total_seconds() / 60))
        endpoints = sorted({p["endpoint"] for p in ps})
        motivos = sorted({p["motivo"] for p in ps if p["motivo"]})
        causa = (f"coincide con el reinicio de la API ({arranco:%H:%M} UTC)"
                 if reinicio else ", ".join(motivos) or "sin motivo informado")
        out.append(Hallazgo(
            sujeto=vista, regla="vista_ciega",
            severidad="alta" if (len(personas) >= 3 or minutos >= 10) else "media",
            nombre=vista,
            problema=(f"ciega {minutos} min ({desde:%H:%M} a {ultimo:%H:%M} UTC) · "
                      f"{len(personas) or len(ps)} pantalla/s abierta/s · causa: {causa} · "
                      f"{reloj.hhmm(ahora)}"),
            detalle=" · ".join(f"{e} ({', '.join(motivos) or '?'})" for e in endpoints),
            que_hacer=("Si la causa es el reinicio de la API, nada: pasa solo. Si no, "
                       "el pedido que falla es del proxy de Next o del backend: mirar "
                       f"{endpoints[0]} y el log de la API a esa hora."),
            evidencia={"personas": sorted(personas), "pulsos": len(ps),
                       "desde": desde.isoformat(), "ultimo": ultimo.isoformat(),
                       "endpoints": endpoints, "motivos": motivos,
                       "api_arranco_at": arranco.isoformat() if arranco else None}))
    return out


# ═══ pantalla_tildada ══════════════════════════════════════════════════════
def pantalla_tildada(u: dict) -> list[Hallazgo]:
    """**La pantalla que no responde** — el otro «se me colgó la app» (§0.dm).

    `vista_ciega` cubre la mitad que el servidor puede ver de refilón: los
    pedidos fallan. Esta es la mitad que **no pasa por el servidor jamás**: el
    hilo principal del navegador queda bloqueado, no falla nada, no hay request,
    no hay excepción — y la persona ve la app clavada y aprieta F5.

    Sin este reporte las dos llegan como la misma frase y se arreglan en lugares
    opuestos: una es del backend o de la red, la otra es JS de esta app.

    Lo mide `lib/tilde.ts` en el navegador (hueco entre latidos + `longtask`) y
    llega por el mismo `POST /api/pulso` con `tipo = 'tilde'`.

    ⚠️ **HABILIDAD PROPIA, y no una regla más adentro de `latencia`.** Nació
    ahí y estuvo mal media hora: `latencia` mira TRES cosas y, con el tilde
    adentro, no poder leer **una** tabla nueva la dejaba entera en «no pude
    mirar» — o sea, la degradación de endpoints y los 5xx, que son lo importante
    y se leen de otro lado, se apagaban por una fuente secundaria. Lo cantó
    `test_vista_ciega_...` en el primer `pytest`, y la lección es la del
    invariante 1 puesta al revés: si «no pude mirar» no puede cerrar nada,
    tampoco puede ser CONTAGIOSO. Una habilidad = una fuente que puede faltar.

    ⚠️ **No tiene arreglo, y eso está declarado**: el agente no puede tocar el
    navegador de nadie. Es un aviso — vive en AHORA, no en ENCONTRÓ.
    """
    from agente import fuentes

    ventana = int(u.get("tilde_ventana_min", 60))
    filas = fuentes.tildes(ventana)
    if filas is None:
        raise SinDatos("no pude leer los tildes de agente.pulso_cliente")
    if not filas:
        return []
    ahora = reloj.ahora_utc()

    por_vista: dict[str, list[dict]] = {}
    for f in filas:
        por_vista.setdefault(f["vista"] or "?", []).append(f)

    out = []
    for vista, ts in sorted(por_vista.items()):
        personas = {t["email"] for t in ts if t["email"]}
        peor_ms = max((t["ms"] or 0) for t in ts)
        total_s = sum((t["ms"] or 0) for t in ts) / 1000
        # ¿Fue JS de la app? Lo dice el `longtask` que el navegador midió DENTRO
        # del hueco. Sin tarea larga el hilo se fue en otra cosa (memoria, GC) y
        # buscar el render caro sería buscar donde no está.
        peor_tarea = max((int(t["datos"].get("peor_tarea_ms") or 0)) for t in ts)
        memorias = [int(t["datos"]["memoria_mb"]) for t in ts
                    if isinstance(t["datos"].get("memoria_mb"), int)]
        es_js = peor_tarea >= peor_ms / 2 if peor_ms else False
        ultimo = max(t["at"] for t in ts)

        out.append(Hallazgo(
            sujeto=vista, regla="pantalla_tildada",
            severidad="alta" if (peor_ms >= 10_000 or len(ts) >= 3) else "media",
            nombre=vista,
            problema=(f"se clavó {len(ts)} vez/veces en {ventana} min · "
                      f"la peor {peor_ms / 1000:.1f} s · "
                      f"{len(personas) or len(ts)} pantalla/s · "
                      f"{'JS de la app' if es_js else 'sin tarea larga'} · "
                      f"{reloj.hhmm(ahora)}"),
            detalle=" · ".join(sorted({t["motivo"] for t in ts if t["motivo"]})),
            que_hacer=(
                "Es el NAVEGADOR, no la API: mirar en el backend es mirar donde "
                "no está. Si dice «JS de la app», el hilo se fue en una tarea de "
                f"{peor_tarea / 1000:.1f} s de esa vista — el sospechoso es lo que "
                "esa pantalla calcula o dibuja en cada refresco (tabla grande, "
                "payload gordo, chart que se re-monta). Si dice «sin tarea larga» "
                "y la memoria del reporte viene creciendo, es una fuga de la "
                "pestaña y se confirma dejándola abierta y mirando cómo sube."),
            evidencia={"episodios": len(ts), "peor_ms": peor_ms,
                       "trabado_total_s": round(total_s, 1),
                       "peor_tarea_ms": peor_tarea,
                       "memoria_mb": memorias[-1] if memorias else None,
                       "memoria_min_mb": min(memorias) if memorias else None,
                       "memoria_max_mb": max(memorias) if memorias else None,
                       "personas": sorted(personas),
                       "ultimo": ultimo.isoformat()}))
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
    # ⚠️⚠️ **DOS PREGUNTAS DISTINTAS SOBRE LO MISMO, Y LA SEGUNDA NO CADUCA.**
    #
    # «Estaba hace 24 h y ahora no» sirve UN DÍA: la referencia se mueve. Una
    # tabla borrada el martes 19:00 se ve el miércoles al mediodía y deja de
    # verse el miércoles a la noche, porque a esa altura «hace 24 h» ya es un
    # mundo sin la tabla. Después, silencio para siempre — y la tabla sigue sin
    # estar.
    #
    # «¿Está la que el sistema dice que tiene que estar?» se contesta SIEMPRE, y
    # la respuesta la tiene `sql/schema.sql`. Las dos conviven: la primera
    # atrapa lo que no está declarado, la segunda no vence nunca.
    faltantes = {t for t in peso.declaradas() if t not in hoy}
    for tabla in sorted(faltantes):
        b = ayer.get(tabla)
        out.append(Hallazgo(
            sujeto=tabla, regla="desaparecio", severidad="alta",
            problema=("la tabla NO está y `sql/schema.sql` dice que tiene que "
                      f"existir · {reloj.hhmm()}"),
            detalle=(f"hace 24 h pesaba {peso.mb(b)}" if b else
                     "tampoco estaba hace 24 h: se borró antes de ayer"),
            que_hacer=("Si fue a propósito, sacarla también de `sql/schema.sql` "
                       "(un `DROP TABLE IF EXISTS`) y el aviso se va solo. Si "
                       "no, `apply_schema` la vuelve a crear vacía — y ahí falta "
                       "ver qué pasó con lo que tenía."),
            evidencia={"bytes_antes": b, "fuente": "schema"}))

    # Y lo que se borró sin estar declarado: acá el único testigo es la foto.
    for tabla, b in ayer.items():
        if tabla not in hoy and tabla not in faltantes:
            out.append(Hallazgo(
                sujeto=tabla, regla="desaparecio", severidad="alta",
                problema=f"la tabla ya NO está · hace 24 h pesaba {peso.mb(b)}",
                detalle=("no está declarada en `sql/schema.sql`, así que este "
                         "aviso solo dura 24 h: es el único rastro que queda"),
                que_hacer="Si fue a propósito, ignorala. Si no, alguien dropeó "
                          "algo.",
                evidencia={"bytes_antes": b, "fuente": "foto_24h"}))

    out += _peso_total(hoy)
    peso.guardar(hoy)
    return out


def _delta_col(delta: int, hay_referencia: bool) -> str:
    """El delta de 7 días de UNA fila del listado, con signo — o `—` si
    todavía no hay con qué comparar (primera medición)."""
    from agente import peso

    if not hay_referencia:
        return "—"
    if delta == 0:
        return "="
    return f"{'+' if delta > 0 else '−'}{peso.mb(abs(delta))}"


def _listado_por_vista(grupos: list[dict], *, hay_ref: bool) -> str:
    """El `detalle` de `peso_total_*`: el peso consolidado por VISTA de la
    página (`AGENT.md` §0.eg), con su delta de 7 días y, debajo, hasta 3
    tablas indentadas — en vez de 5 tablas sueltas sin decir a qué pantalla
    pertenecen."""
    from agente import peso

    ancho = max([len("VISTA / tabla")] +
                [len(g["vista"]) for g in grupos] +
                [len(t["tabla"]) + 2 for g in grupos for t in g["tablas"]])
    filas = [f"{'VISTA / tabla':<{ancho}}  {'PESA':>10}  {'7 DÍAS':>10}"]
    for g in grupos:
        filas.append(f"{g['vista']:<{ancho}}  {peso.mb(g['bytes']):>10}  "
                      f"{_delta_col(g['delta'], hay_ref):>10}")
        for t in g["tablas"]:
            filas.append(f"  {t['tabla']:<{ancho - 2}}  {peso.mb(t['bytes']):>10}  "
                          f"{_delta_col(t['delta'], hay_ref):>10}")
    return "\n".join(filas)


def _peso_total(hoy: dict[str, int]) -> list[Hallazgo]:
    """El tamaño de TODA la base, dos veces por día (11 y 16, hora de la mesa).

    Pedido del user (2026-08-28). El dato se venía midiendo y guardando cada
    hora desde siempre — lo que faltaba era **dónde verlo**: el módulo decía que
    el peso «viaja aparte» y ese aparte nunca se construyó.

    Es un AVISO: no hay nada que apretar. Va a AHORA con su fecha y su hora, y
    al día siguiente se vacía solo como todo lo demás.
    """
    from agente import peso

    franja = peso.franja_de_hoy()
    if not franja:
        return []                      # todavía no pasó ninguna franja de hoy

    total = sum(hoy.values())
    # Contra la semana pasada, no contra ayer: un día no dice nada de una
    # tendencia. Y si la serie todavía no llegó a los 7 días, se compara contra
    # la foto más vieja que haya DICIENDO de cuándo es — un «todavía no» el
    # lector no lo puede distinguir de un error, y encima no sirve para nada.
    vieja, horas = peso.referencia()
    antes = sum(vieja.values()) if vieja else 0
    if antes:
        cuando = (f"{horas // 24} días" if horas >= 24 else f"{horas} h")
        delta = (f" · {'+' if total >= antes else ''}{peso.mb(total - antes)} "
                 f"en {cuando} (era {peso.mb(antes)})")
    else:
        delta = " · es la primera medición: el próximo aviso ya compara"

    top = sorted(hoy.items(), key=lambda x: -x[1])[:5]
    # ⚠️ **LA QUE CRECIÓ NO ES LA QUE PESA.** El aviso pedía «mirar qué creció»
    # y la evidencia sólo traía las cinco MÁS GRANDES — que son casi siempre las
    # mismas y casi nunca las que se movieron. Con eso, la pregunta que el propio
    # texto deja abierta no se podía contestar ni a mano: lo confirmó el primer
    # texto redactado por el modelo (§0.dn), que no nombró una sola tabla porque
    # el dato no estaba. `peso.referencia()` YA devuelve la foto vieja tabla por
    # tabla y se estaba usando sólo para sumarla.
    #
    # Va APARTE de `top` a propósito: son dos preguntas distintas —«qué es
    # grande» y «qué se movió»— y juntarlas en una lista fue el error original.
    crecio = [(t, hoy[t] - vieja.get(t, 0)) for t in hoy] if vieja else []
    crecio = sorted((x for x in crecio if x[1] > 0), key=lambda x: -x[1])[:5]
    # ⚠️ **EL SUJETO ES LA BASE; LA FRANJA VA EN LA REGLA.**
    #
    # La primera versión ponía `sujeto=franja` y tenía que escribir el `nombre`
    # a mano («peso de la base») — que es exactamente la firma que el test
    # `test_de_que_es_el_problema_nunca_se_escribe_a_mano` prohíbe: si hay que
    # redactar de qué es el problema, el sujeto está mal elegido. Y lo estaba:
    # «16:00» no es de qué se habla, es cuándo.
    #
    # Con la franja en la REGLA, el trío sigue siendo distinto entre las 11 y
    # las 16 —que es lo que hace que el segundo aviso nazca en vez de pisar al
    # primero— y el sujeto dice lo que se está midiendo.
    por_vista = peso.por_vista(hoy, vieja)
    return [Hallazgo(
        sujeto="la base", regla=f"peso_total_{franja[:2]}", severidad="baja",
        problema=f"la base pesa {peso.mb(total)} en {len(hoy)} tablas{delta}",
        # Consolidado por VISTA de la página (pedido del user, `AGENT.md`
        # §0.eg): qué pantalla concentró el crecimiento y con qué tablas
        # adentro, en vez de 5 tablas sueltas sin decir a qué pantalla
        # pertenecen.
        detalle=_listado_por_vista(por_vista, hay_ref=bool(vieja)),
        que_hacer=("Leer el listado: qué vista concentra el salto de la semana "
                   "y qué tabla adentro. Si una tabla crece sola y no es un "
                   "histórico (timesales, *_bars_1m, *_hist), mirar quién la "
                   "escribe."),
        evidencia={"bytes": total, "tablas": len(hoy), "franja": franja,
                   "bytes_hace_7d": antes,
                   "top": [{"tabla": t, "bytes": b} for t, b in top],
                   "crecio": [{"tabla": t, "crecio_mb": peso.mb(d)}
                              for t, d in crecio],
                   "por_vista": por_vista})]


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
