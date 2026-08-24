"""api/services/av_agent_agenda.py — QUÉ ESTÁ HACIENDO EL AGENTE, HOY.

Doc madre: `docs/AV_AGENT.md` §0.bv.

Pedido del user (2026-08-22): *«que figure todo lo que el agente está
monitoreando durante el día, actualización de la última vez y eso… es como si
viniera mi jefe y me diga qué estás haciendo y vea desglosado todo lo que hago.
De esa manera alguien puede ver fácil si hay algo que NO está haciendo»*.

**La última frase es la que define el diseño.** Una lista de capacidades ya
existe (la tab SKILLS: *qué sé hacer*). Lo que faltaba es la otra pregunta —
*¿lo estoy haciendo?*— y esa **solo se contesta cruzando el catálogo con las
corridas reales**. Una capacidad que nadie ejecuta se ve idéntica a una que
corre cada cinco minutos, y ese es exactamente el hueco que el user quiere
tapar.

    SKILLS    qué sé hacer            (catálogo)
    CONTROL   qué estoy haciendo HOY  (catálogo × corridas × hallazgos)

## Todo DERIVADO. Ninguna lista nueva.

Agregar un detector, un control o un cron y que **no** aparezca acá sería
volver al problema: la pantalla diría que el agente hace 30 cosas mientras hace
31, y nadie lo notaría. Las cuatro fuentes ya existen y ya se mantienen solas:

    av_agent_skills.catalogo()   qué mira cada pieza y en qué job corre
    jobs_catalogo                el schedule REAL, leído del crontab
    manager.job_runs             cuándo corrió de verdad y cómo salió
    agente.av_agent_items       qué encontró, por origen

## Agrupado por RITMO, no por dominio

El eje es **el job**, porque es la unidad que responde «¿corrió?». Un detector
no corre solo: corre adentro de un job, y si ese job está caído los doce
detectores que viven ahí están ciegos **al mismo tiempo**. Agrupar por dominio
mostraría doce filas rojas sin decir que son una sola causa.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# Cuánto puede pasar de la ventana esperada antes de decir que se atrasó. El
# número sale de la cadencia declarada del propio job, no de una constante: un
# cron que corre cada 5 minutos y uno diario no se juzgan con la misma vara.
TOLERANCIA = 2.5

# El LATIDO no es un job del crontab: es un daemon. Se agrega a mano porque es
# la pieza que más corre de todas y no aparecería nunca en `catalogo_jobs`.
_DAEMON = "centinela (vigilancia en vivo)"


def _seg(cron: str) -> float | None:
    """Cada cuántos SEGUNDOS corre, leído del schedule del crontab.

    Solo entiende las formas que el repo usa de verdad (`*/N`, horas listadas,
    diario). Lo que no puede leer devuelve `None` y **no se juzga**: decir «se
    atrasó» sobre un schedule que no supe interpretar sería inventar una alarma.
    """
    c = (cron or "").strip()
    if not c:
        return None
    minuto = c.split()[0] if c.split() else ""
    if minuto.startswith("*/"):
        try:
            return float(minuto[2:]) * 60
        except ValueError:
            return None
    partes = c.split()
    if len(partes) >= 2:
        horas = partes[1]
        if horas == "*":
            return 3600.0
        if "-" in horas or "," in horas:
            # `13-20` o `12,16,20` → corre N veces por día, tomamos el hueco
            # más grande como referencia (el peor caso legítimo).
            return 3600.0 * 4
        return 86400.0
    return None


def _del_daemon(piezas_por_job: dict[str, list[dict]]) -> list[dict]:
    """Las piezas que el CENTINELA mira en cada pasada.

    Sale de `av_agent_centinela._CUBRE`, que es el catálogo de QUÉ VIGILA el
    daemon. Es la única clase de lista de la que uno se puede fiar: la que ya
    tiene otro dueño que la mantiene — el mismo `_CUBRE` decide qué tipos lee
    la pantalla AHORA y qué cuenta el semáforo, así que si queda vieja se nota
    en el tablero antes que acá.

    ⚠️ **Ya NO decide qué se puede dar por cerrado** (§0.dh): eso lo declara
    cada detector al terminar bien (`evaluados`). Son dos preguntas distintas
    —*qué vigilo* y *qué pude mirar recién*— y una sola lista no puede
    contestar las dos sin mentir en una.
    """
    try:
        from api.services.av_agent_centinela import _CUBRE
        mira = {t for tipos in _CUBRE.values() for t in tipos}
    except Exception as e:
        logger.warning("agenda: no pude leer qué mira el centinela (%s)", e)
        return []
    vistos: set[str] = set()
    out: list[dict] = []
    for piezas in piezas_por_job.values():
        for p in piezas:
            if p.get("tipo") in mira and p["nombre"] not in vistos:
                vistos.add(p["nombre"])
                out.append(p)
    return out


def _edad_s(iso: str | None, ahora: datetime) -> float | None:
    if not iso:
        return None
    try:
        return (ahora - datetime.fromisoformat(iso)).total_seconds()
    except ValueError:
        return None


def vista() -> dict:
    """Lo que el agente monitorea, con su ritmo, su última corrida y su
    resultado. **Nunca levanta**: una pantalla de control que se cae con la
    primera fuente rota no controla nada."""
    ahora = datetime.now(UTC)
    piezas_por_job: dict[str, list[dict]] = {}
    total_skills = 0
    try:
        from api.services import av_agent_skills
        for s in av_agent_skills.catalogo():
            total_skills += 1
            job = (s.extra or {}).get("corre_en") or ""
            if s.id.startswith("detectar.control."):
                job = "jobs.controles_datos"      # todos los controles ahí
            if not job:
                continue                          # no es algo que CORRA solo
            pieza = {"nombre": s.nombre, "que_hace": s.que_hace,
                     "dominio": s.dominio, "usa_ia": s.usa_ia,
                     # El TIPO del detector, para poder cruzarlo con lo que el
                     # daemon declara que mira. `detectar.sin_precio` →
                     # `sin_precio`; los controles y el resto no matchean y no
                     # tienen por qué.
                     "tipo": s.id.split(".", 1)[-1]}
            piezas_por_job.setdefault(job, []).append(pieza)
    except Exception as e:
        logger.warning("agenda: sin catálogo de skills (%s)", e)

    # Cuántos hallazgos ABIERTOS dejó cada job. Es la diferencia entre «corrió»
    # y «sirvió»: un job verde que hace un mes no encuentra nada puede estar
    # mirando una tabla vacía.
    abiertos: dict[str, int] = {}
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT origen, count(*) FROM agente.av_agent_items "
                        "WHERE estado NOT IN ('resuelto','ignorado') "
                        "GROUP BY origen")
            abiertos = {(r[0] or ""): r[1] for r in cur.fetchall()}
    except Exception as e:
        logger.warning("agenda: sin items (%s)", e)

    filas: list[dict] = []
    # ⚠️ **EL RITMO NO DEPENDE DE LA BASE, LA CORRIDA SÍ.** `catalogo_jobs()`
    # joinea el crontab con `manager.job_runs`, así que si Postgres no contesta
    # levanta ENTERO y la pantalla queda en blanco — o sea que el día que la base
    # está caída, la tab que existe para decir «hay algo que no está corriendo»
    # es la que menos dice. El crontab viaja con el deploy y se parsea sin tocar
    # nada: se cae a esa lista, y cada rutina sale con `atrasado=None`
    # (SIN PODER JUZGAR), que es la verdad exacta — sé qué debería correr, no sé
    # si corrió.
    jobs: list[dict] = []
    try:
        from api.services.jobs_catalogo import catalogo_jobs
        jobs = catalogo_jobs().get("jobs") or []
    except Exception as e:
        logger.warning("agenda: sin corridas, uso solo el crontab (%s)", e)
        try:
            from api.services.jobs_catalogo import schedules_por_modulo
            jobs = [{"schedule": (crons[0] if crons else ""),
                     "runs": [{"modulo": mod, "ultimo": None}]}
                    for mod, crons in schedules_por_modulo().items()]
        except Exception as e2:
            logger.warning("agenda: tampoco pude leer el crontab (%s)", e2)
    try:
        for j in jobs:
            cron = j.get("schedule") or j.get("cron") or ""
            for r in (j.get("runs") or []):
                mod = r.get("modulo") or ""
                piezas = piezas_por_job.get(mod) or []
                if not piezas:
                    continue          # el agente no monitorea nada ahí
                ult = r.get("ultimo") or {}
                edad = _edad_s(ult.get("started_at"), ahora)
                cada = _seg(cron)
                filas.append({
                    "job": mod, "cada": cron,
                    "ultima": ult.get("started_at"),
                    "estado": ult.get("status"),
                    "resumen": ult.get("resumen") or "",
                    "hace_s": int(edad) if edad is not None else None,
                    # ⚠️ `None` cuando no se puede juzgar: sin schedule legible
                    # o sin ninguna corrida, «atrasado» sería una afirmación
                    # sin respaldo. Es distinto de `False`.
                    "atrasado": (None if (edad is None or cada is None)
                                 else edad > cada * TOLERANCIA),
                    "piezas": sorted(piezas, key=lambda p: p["nombre"]),
                    "encontrados": abiertos.get(mod, 0),
                })
    except Exception as e:
        logger.warning("agenda: sin catálogo de jobs (%s)", e)

    # EL DAEMON, aparte: no está en el crontab y es el que más corre.
    #
    # ⚠️ **La fila existe AUNQUE el latido no se pueda leer** (2026-08-22). Con
    # el `try` alrededor de todo, una base caída hacía DESAPARECER la fila del
    # centinela — y con ella sus piezas, incluida `actividad`, que no corre en
    # ningún cron: la pantalla decía que el agente hace 30 cosas cuando hace
    # 31, sin fallar. La fila se arma SIEMPRE; lo que se degrada es el estado
    # («sin datos», `atrasado=None` — no pude juzgar ≠ está bien).
    fila_daemon = {
        "job": _DAEMON, "cada": "", "ultima": None, "estado": None,
        "resumen": "sin datos del latido", "hace_s": None, "atrasado": None,
        # ⚠️ **LO QUE MIRA EL DAEMON SALE DE `_CUBRE`, NO DEL CRON.**
        # Acá decía `piezas_por_job["jobs.av_agent_live"]`, que es el
        # cron de rueda — otra cosa. Le colgaba 8 piezas que el daemon
        # no corre y le faltaban las 2 que sí (`tasa_sospechosa` y
        # `salud`, que corren de noche en otro job). `_CUBRE` es la
        # lista que el propio `_observar()` usa para decidir qué
        # cerrar, así que no puede quedar vieja sin romper otra cosa
        # antes — que es la única clase de lista que se puede leer.
        "piezas": sorted(_del_daemon(piezas_por_job),
                         key=lambda p: p["nombre"]),
        "encontrados": abiertos.get("live", 0),
    }
    try:
        from api.services.av_agent_centinela import estado as cent_estado
        c = cent_estado(limite=1)
        lat = c.get("latido") or {}
        if lat:
            fila_daemon.update({
                "cada": f"cada {lat.get('cadencia_s')}s",
                "ultima": lat.get("at"),
                "estado": "ok" if c.get("vivo") else "error",
                "resumen": (f"ciclo {lat.get('ciclo')}"
                            + (" · en rueda" if lat.get("en_rueda")
                               else " · fuera de rueda")),
                "hace_s": lat.get("hace_s"),
                "atrasado": not c.get("vivo"),
            })
    except Exception as e:
        logger.warning("agenda: sin latido (%s)", e)
    filas.insert(0, fila_daemon)

    # ⚠️ **ÚNICAS, no la suma de las filas.** Un detector puede correr en dos
    # lados de verdad (el centinela mira `sin_precio` en rueda y el cron lo
    # vuelve a mirar): mostrarlo en las dos filas es correcto —corre dos veces—
    # pero contarlo dos veces infla el titular.
    monitoreadas = len({p["nombre"] for f in filas for p in f["piezas"]})
    return {
        "ok": True,
        "filas": filas,
        "piezas": monitoreadas,
        # ⚠️ **LA DIFERENCIA CON SKILLS SE DECLARA, NO SE ESCONDE.** Acá salen
        # solo las habilidades que CORREN SOLAS; las demás (explicar un cálculo,
        # mandar un mensaje) existen y se usan a pedido. Sin este número, alguien
        # que viene de SKILLS ve 35 allá y 32 acá y no sabe si faltan tres o si
        # tres no corren — y esta pantalla existe justamente para contestar eso.
        "a_pedido": max(0, total_skills - monitoreadas),
        # ⚠️ Los tres números que contestan «¿está haciendo su trabajo?». El de
        # `sin_juzgar` va SIEMPRE aunque sea 0: sin él, «0 atrasados» se lee
        # como «todo al día» cuando puede ser «no pude mirar tres».
        "atrasados": sum(1 for f in filas if f["atrasado"] is True),
        "sin_juzgar": sum(1 for f in filas if f["atrasado"] is None),
        "al_dia": sum(1 for f in filas if f["atrasado"] is False),
    }
