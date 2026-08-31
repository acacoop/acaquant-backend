"""agente/rehacer.py — RELANZAR UN JOB, PERO CON LA PRUEBA EN LA MANO.

Doc madre: **`docs/AV_AGENT.md`** §0.ar.

EL PEDIDO, Y LA CONDICIÓN QUE LO HACE SEGURO
============================================

El user (2026-08-20), después de ver que el AuM del día no se había escrito:

    *«que mismo tenga la skill o que lo pueda hacer (o sea, ejecutar fecha de hoy
    por haber detectado un error Y haber verificado 100% en la base que no hay
    fecha realmente con lo que iba de hoy)»*

**Esa segunda mitad es el diseño entero.** No se relanza porque el job falló: se
relanza porque **se miró la tabla y el dato NO ESTÁ**. Son dos cosas distintas y
confundirlas es lo que hace peligroso a un botón de relanzar:

    el job falló        → una señal del PROCESO. Puede fallar y haber escrito.
    el dato no está     → un hecho sobre el RESULTADO. Es lo único que importa.

Un job que sale con error después de escribir todo (un cleanup que revienta al
final) no necesita relanzarse; uno que sale en verde sin escribir nada, sí. Por
eso **la precondición se consulta contra la base, siempre, y manda sobre el
estado del job**. Es la misma ley que los CONTRATOS de SALUD: se chequea el
RESULTADO, no el proceso.

LAS CUATRO GUARDAS
==================

  1. **Sin evidencia no corre.** Si la fecha YA está en la tabla, no se ejecuta
     nada y se dice que no hacía falta. Esa es la guarda principal.
  2. **Por `run_job.sh`**, que trae lock y timeout (REGLA #4). Si la corrida
     anterior sigue viva, esta se saltea sola en vez de apilarse — que es
     exactamente el incidente de CPU del 2026-06-03.
  3. **Solo jobs declarados** (`REHACIBLES`). No es un ejecutor de comandos: es
     una lista corta de jobs idempotentes, con su tabla y su columna de fecha.
     Un `subprocess` con el comando abierto sería una consola remota.
  4. **Se verifica MIRANDO LA TABLA DE NUEVO.** Que el proceso salga 0 no prueba
     nada: la prueba es que la fecha ahora esté.

⚠️ **NO relanza motores.** Un motor en rueda le corta el feed de precios a la
mesa (regla del user, 2026-08-18) y eso no se decide desde un botón. Estos son
jobs de una sola pasada, idempotentes por diseño.
"""
from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

RUN_JOB = "/root/TradingAV/deploy/run_job.sh"
# ⚠️⚠️ **EL JOB NO ESCRIBE EN STDOUT: ESCRIBE EN ESTE ARCHIVO.**
#
# `run_job.sh` redirige TODO —lo del job y lo suyo— con `>> "$LOG"`, así que un
# `subprocess.run(capture_output=True)` sobre el wrapper captura **la cadena
# vacía**. Es lo que pasó el 2026-08-28: se hizo viajar `salida` hasta la
# pantalla y lo que llegaba era nada, porque nunca hubo nada que capturar.
#
# La explicación vive en `logs/<label>.log`, y para saber qué parte es de ESTA
# corrida se anota el tamaño del archivo antes de largar y se lee de ahí en
# adelante. Leer «las últimas N líneas» a secas traería las de ayer cuando el
# job no llegue a escribir una sola.
LOGS = "/root/TradingAV/logs"

# ⚠️⚠️ **CUÁNTO SE ESPERA, Y POR QUÉ ES TAN POCO.**
#
# Acá había `30 * 60`, y era una fantasía: **del otro lado nadie espera 30
# minutos.** El proxy de Next que sirve `/api/agente` declara `maxDuration = 30`
# (segundos), así que el request se corta muchísimo antes y el que apretó el
# botón no se entera de nada.
#
# Medido el 2026-08-28 corriéndolo a mano: `portafolio_diario` tarda **502
# segundos**. O sea que este arreglo NUNCA podía contestar a tiempo, y cada
# intento moría de una forma distinta —una vez sin explicación, otra con un
# SIGTERM— buscando el bug en el job, que funcionaba perfecto.
#
# Un trabajo de ocho minutos no es un request HTTP. Se LARGA y se confirma
# después: es para lo que existe `Resultado(inmediato=False)`, que deja el
# hallazgo en `en_curso` hasta que el detector no lo vea más — y ahí se cierra
# POR ACCIÓN, que es justo lo que queremos anotar.
ESPERA_CORTA_S = 20


# Los jobs que el agente puede rehacer. **Cortos, idempotentes y con su prueba**:
# la tabla y la columna donde se ve si el día está. Sin esas dos no entra —
# porque sin ellas no hay forma de exigir la precondición ni de verificar.
REHACIBLES: dict[str, dict] = {
    "portafolio_diario": {
        "titulo": "Tenencias del día (AuM)",
        # El label del crontab, que es lo que `run_job.sh` recibe.
        "label": "portafolio_diario",
        "timeout": "25m",
        "comando": ("cd /root/TradingAV && /root/TradingAV/venv/bin/python "
                    "-m jobs.portafolio_backfill --diario"),
        "tabla": "portafolio.tenencia",
        "columna": "fecha",
        # ⚠️ **QUÉ DÍA ESCRIBE, que NO es hoy.** `--diario` snapshotea el día
        # hábil ANTERIOR (el cierre de ayer). Exigirle el día de hoy lo daría
        # por faltante TODAS las noches — un detector que grita siempre enseña
        # a ignorar la lista entera, que es la enfermedad que el agente vino a
        # curar. El dato vive con el job, no con el que pregunta.
        "dia": "habil_anterior",
        # A qué hora UTC corre su cron (deploy/crontab.txt). Es lo que deja
        # calcular la ÚLTIMA CORRIDA ESPERADA — sin esto, el control le exigía
        # el viernes a un sábado en que el job ni corre (§0.cq).
        "corre_utc": 11,
        "corre_dias": "L-V",
        # Cuánto tarda de verdad. Medido a mano el 2026-08-28: 1.885 cuentas,
        # 502 s. **No es cosmético**: es lo que dice si el botón puede contestar
        # dentro de un request HTTP (no puede) y lo que se le muestra al que lo
        # aprieta para que sepa cuánto esperar.
        "dura_aprox_s": 502,
        # ⚠️⚠️ **LOS OTROS NOMBRES DEL MISMO JOB.** Ver `cual_job()`.
        "conocido_como": (
            "jobs.portafolio_backfill",   # diagnostico_registry.Pieza.unidad
            "aum",                        # manager.job_runs.tipo (legado)
            "job:portafolio_diario",      # el id del chequeo de api/services/salud
        ),
        "rompe": ("sin esto el AuM, la Tenencia Valorizada y Títulos en Alquiler "
                  "se quedan con el día anterior"),
    },
}


# ⚠️⚠️ **EL ÁRBITRO DE NOMBRES — REGLA #9(B) adentro del agente.**
#
# `portafolio_diario` se llama de CUATRO formas distintas según quién lo mire:
# el label del cron, el `unidad` de la Pieza de diagnóstico, el `tipo` con el
# que se anota en `manager.job_runs` y el id del chequeo de salud. Cada mitad
# del sistema era coherente consigo misma, así que no fallaba nada: el detector
# normalizaba `jobs.portafolio_backfill` → `portafolio_backfill`, buscaba esa
# clave acá, no la encontraba, y **la tarjeta salía sin botón** diciendo que era
# un motor. El arreglo existía, andaba, y no había forma de llegar a él.
#
# Por eso la traducción vive UNA sola vez y todos preguntan acá. Un nombre nuevo
# es una entrada en `conocido_como`, no una regla de string más en otro archivo.
_INDICE: dict[str, str] = {}


def cual_job(nombre: str) -> str:
    """De cualquiera de sus nombres al job. Vacío si no es relanzable.

    Es la ÚNICA normalización del repo: el detector la usa para decidir si hay
    botón y el arreglo para decidir qué corre. Cuando cada uno tenía la suya
    —los dos hacían `split(":")` y `removeprefix("jobs.")`, iguales y en dos
    archivos— igual discrepaban con la realidad, porque el alias verdadero no
    era ninguna de esas dos transformaciones.
    """
    if not _INDICE:
        for k, cfg in REHACIBLES.items():
            _INDICE[k] = k
            for alias in cfg.get("conocido_como") or ():
                _INDICE[alias] = k
    n = (nombre or "").strip()
    if not n:
        return ""
    # Directo o por alias. El fallback de sufijos se conserva por si aparece un
    # nombre nuevo del mismo job, pero NO es el mecanismo: el mecanismo es la
    # declaración.
    return (_INDICE.get(n)
            or _INDICE.get(n.split(":")[-1].removeprefix("jobs."), ""))


def proximo_intento(job: str, ahora: datetime | None = None) -> str:
    """Cuándo vuelve a correr **solo**. En castellano, listo para mostrar.

    ⚠️ **`deploy/run_job.sh` NO reintenta**: toma el lock, corre con timeout,
    loguea `OK/SKIP/TIMEOUT/ERROR` y sale. O sea que para TODOS los jobs del
    crontab la respuesta a «¿lo intenta de nuevo?» es la misma: no — lo único
    que hay es su próxima corrida programada.

    Decirlo importa porque es lo que separa «esperá» de «hacelo vos», y hoy la
    tarjeta no lo decía en ningún lado: había que saberse el crontab de memoria.
    """
    from core.calendario import es_habil

    cfg = REHACIBLES.get(job)
    if not cfg or "corre_utc" not in cfg:
        return ""
    ahora = ahora or datetime.now()
    h = int(cfg["corre_utc"])
    d = ahora.date()
    # Si la de hoy ya pasó (o hoy no es hábil), la próxima es el hábil siguiente.
    if not es_habil(d) or ahora.hour >= h:
        d += timedelta(days=1)
        while not es_habil(d):
            d += timedelta(days=1)
    cuando = "hoy" if d == ahora.date() else d.isoformat()
    return f"no reintenta solo · próxima corrida programada: {cuando} {h:02d}:00 UTC"


def estado_del_dia(nombre: str) -> dict | None:
    """¿El día que este job tenía que escribir ESTÁ en su tabla?

    `None` solo si el job no es relanzable. Si lo es, contesta SIEMPRE con uno
    de tres estados — y los tres son distintos:

        falta    → el día no está. Hay trabajo, y hay botón.
        esta     → el día está. El job reventó DESPUÉS de escribir: no hay que
                   relanzar nada, por más que la corrida figure en rojo.
        no_pude  → la consulta falló. **No es «falta»**: relanzar por una
                   lectura que no se pudo hacer es ejecutar a ciegas.

    Antes esto devolvía `None` para los tres casos y para «no es relanzable»,
    así que la tarjeta no podía distinguir «el dato está» de «no sé» — que es la
    diferencia entre no hacer nada y tener que actuar.
    """
    job = cual_job(nombre)
    if not job:
        return None
    cfg = REHACIBLES[job]
    fecha = fecha_objetivo(job)
    if not fecha:
        return None
    hay = hay_dato(job, fecha)
    estado = "no_pude" if hay is None else ("esta" if hay else "falta")
    return {"job": job, "fecha": fecha, "estado": estado,
            "tabla": cfg["tabla"], "rompe": cfg.get("rompe", ""),
            "proximo": proximo_intento(job)}


def fecha_objetivo(job: str, ahora: datetime | None = None) -> str:
    """El día que ESE job tenía que haber escrito. Vacío si no se sabe.

    Se calcula con el MISMO reloj que usa el job (`datetime.now()`, el del
    Droplet) y no con la hora de Buenos Aires: si el diag y el job resolvieran
    la fecha de distinta forma podrían diferir un día entre las 00 y las 03 UTC
    y nadie se enteraría — las dos mitades serían coherentes consigo mismas
    (REGLA #9).

    ⚠️⚠️ **La fecha esperada depende de CUÁNDO CORRIÓ el job por última vez,
    no de qué día es hoy** (§0.cq — el sábado que alertó por el viernes). El
    razonamiento en dos pasos, cada uno con el calendario:

        1. ¿cuál fue la ÚLTIMA CORRIDA esperada?  hoy, solo si hoy es hábil y
           su hora de cron ya pasó (con una hora de gracia); si no, el hábil
           anterior — el cron es L-V: un sábado la última corrida fue el
           viernes, y un lunes a las 9 UTC también.
        2. esa corrida escribe el hábil ANTERIOR a sí misma (T-1).

    Antes solo existía el paso 2 aplicado a HOY: un sábado exigía el viernes,
    que recién se escribe el lunes — y la alerta era falsa. Medido
    (medido en prod, 2026-08-22): todos los viernes históricos están
    (14/08, 07/08); el máximo en sábado es el jueves, que es EXACTAMENTE lo
    que este cálculo espera.
    """
    from core.calendario import es_habil

    cfg = REHACIBLES.get(job)
    if not cfg or cfg.get("dia") != "habil_anterior":
        return ""
    ahora = ahora or datetime.now()
    d = ahora.date()
    # 1. la última corrida esperada
    if not es_habil(d) or ahora.hour < int(cfg.get("corre_utc", 11)) + 1:
        d -= timedelta(days=1)
        while not es_habil(d):
            d -= timedelta(days=1)
    # 2. esa corrida escribe el hábil anterior a sí misma
    d -= timedelta(days=1)
    while not es_habil(d):
        d -= timedelta(days=1)
    return d.isoformat()


def hay_dato(job: str, fecha: str) -> bool | None:
    """¿La tabla de ese job tiene ESA fecha? **`None` = no pude mirar.**

    `None` no es `False`: relanzar porque no pudimos consultar sería ejecutar a
    ciegas, que es justo lo que este módulo existe para no hacer.
    """
    cfg = REHACIBLES.get(job)
    if not cfg:
        return None
    from core.postgres import get_pool
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            # La tabla y la columna salen del catálogo de acá, NUNCA del
            # llamador: son las dos únicas piezas interpoladas y por eso tienen
            # que venir de una constante del repo.
            cur.execute(
                f'SELECT 1 FROM {cfg["tabla"]} '
                f'WHERE {cfg["columna"]}::date = %s LIMIT 1', (fecha,))
            return cur.fetchone() is not None
    except Exception as e:
        logger.warning("agente/rehacer: no pude mirar %s (%s)", cfg["tabla"], e)
        return None


def rehacer(job: str, fecha: str, *, por: str = "") -> dict:
    """Relanza el job **solo si la fecha falta de verdad**. Nunca levanta."""
    cfg = REHACIBLES.get(job)
    if not cfg:
        return {"ok": False, "error": f"«{job}» no es un job que se pueda rehacer"}

    antes = hay_dato(job, fecha)
    if antes is None:
        # **No sé ≠ falta.** Sin poder mirar, no se ejecuta.
        return {"ok": False, "corrio": False,
                "error": f"no pude consultar {cfg['tabla']}: no ejecuto a ciegas"}
    if antes:
        # La guarda principal. El job puede haber fallado y haber escrito igual.
        return {"ok": True, "corrio": False, "ya_estaba": True,
                "detalle": (f"{cfg['tabla']} ya tiene {fecha}: no hacía falta "
                            f"rehacer nada")}

    r = _correr(cfg)
    if r.get("lanzado"):
        # **No se verifica todavía**: el job sigue corriendo. Afirmar acá que
        # «sigue sin el día» sería medir antes de que termine y mandar a buscar
        # un problema que no existe.
        mins = round((r.get("dura_aprox_s") or 0) / 60) or None
        return {"ok": True, "corrio": True, "lanzado": True, "por": por,
                "detalle": ("lo largué (por `run_job.sh`, con lock y timeout)"
                            + (f" — la última vez tardó ~{mins} min" if mins else "")
                            + f". Cuando termine, {cfg['tabla']} tiene que tener "
                            f"{fecha}: eso lo confirma el agente solo.")}
    if not r["ok"]:
        # `salteado` NO corrió: decir `corrio: True` mandaría a buscar el
        # problema adentro de un job que ni arrancó.
        return {**r, "corrio": not r.get("salteado"), "ya_estaba": False}


    # ⚠️ **LA PRUEBA ES LA TABLA, NO EL EXIT CODE.** Un job puede salir 0 y no
    # haber escrito una fila (la API devolvió vacío, el filtro dejó todo afuera).
    despues = hay_dato(job, fecha)
    if despues is None:
        return {"ok": False, "corrio": True, "salida": r.get("salida", ""),
                "error": "corrió pero no pude verificar si escribió"}
    if not despues:
        # ⚠️ **ACÁ VA LO QUE DIJO EL JOB.** Sin eso, la pantalla dice «no era
        # que no se hubiera ejecutado» y deja al lector exactamente donde
        # empezó: sabe que no es lo que pensaba, y no sabe qué es.
        return {"ok": False, "corrio": True, "escribio": False,
                "salida": r.get("salida", ""),
                "error": (f"corrió sin error y {cfg['tabla']} SIGUE sin {fecha} — "
                          f"el problema no era que no se hubiera ejecutado")}
    return {"ok": True, "corrio": True, "escribio": True, "por": por,
            "detalle": f"{cfg['tabla']} ahora tiene {fecha}", "salida": r["salida"]}


def _correr(cfg: dict) -> dict:
    """Por `run_job.sh`: lock + timeout + log, igual que el cron (REGLA #4)."""
    import pathlib
    if not pathlib.Path(RUN_JOB).exists():
        # Pasa en local y en cualquier máquina que no sea el Droplet. Decirlo es
        # mejor que un `FileNotFoundError` críptico en la pantalla.
        return {"ok": False, "error": f"no existe {RUN_JOB} (¿no es el Droplet?)"}
    log = pathlib.Path(LOGS) / f'{cfg["label"]}.log'
    # El punto de partida: lo que ya estaba escrito NO es de esta corrida.
    desde = log.stat().st_size if log.exists() else 0
    try:
        # ⚠️ `start_new_session=True`: el job queda en su PROPIA sesión, así no
        # se lo lleva puesto una señal dirigida al grupo del proceso que lo
        # largó. (Un `systemctl restart api.service` mata el cgroup entero y de
        # eso no lo salva nadie — pero el job es reanudable: se vuelve a
        # apretar y retoma desde donde iba.)
        p = subprocess.Popen(
            [RUN_JOB, cfg["label"], cfg["timeout"], cfg["comando"]],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}

    try:
        p.wait(timeout=ESPERA_CORTA_S)
    except subprocess.TimeoutExpired:
        # Sigue corriendo, y está bien: se avisa y el detector confirma.
        return {"ok": True, "lanzado": True, "salida": _lo_que_dijo(log, desde),
                "dura_aprox_s": cfg.get("dura_aprox_s", 0)}

    salida = _lo_que_dijo(log, desde)
    # ⚠️ **SALTEAR NO ES CORRER, Y LAS DOS COSAS SALEN 0.** Si la corrida
    # anterior sigue viva, `run_job.sh` escribe SKIP y sale 0 (es su diseño:
    # apilar dos instancias del mismo job fue el incidente de CPU del
    # 2026-06-03). Sin mirarlo, el agente informaría «corrió y no escribió» de
    # algo que ni arrancó — y mandaría a buscar el bug al lugar equivocado.
    if f'SKIP {cfg["label"]}' in salida:
        return {"ok": False, "salteado": True, "salida": salida,
                "error": ("NO corrió: la corrida anterior del mismo job sigue "
                          "viva y el lanzador la saltea para no apilarlas. "
                          "Esperá a que termine y volvé a intentar.")}
    return {"ok": p.returncode == 0, "salida": salida,
            **({} if p.returncode == 0
               else {"error": _por_que_murio(p.returncode)})}


def _por_que_murio(rc: int) -> str:
    """De un número a una frase. **«salió con código -15» no es un diagnóstico.**

    Un `returncode` NEGATIVO no es un error del job: es una SEÑAL que lo mató
    desde afuera, y eso se atiende en un lugar completamente distinto.
    """
    if rc == 124:
        # `timeout(1)` sale 124 cuando mata al comando por exceder su
        # presupuesto. No es un bug del job: es que tardó más de lo declarado.
        return ("el job se pasó de su TIMEOUT y el lanzador lo cortó a mitad de "
                "camino. Lo que alcanzó a escribir quedó; volvé a intentarlo "
                "(se retoma solo desde donde iba).")
    if rc >= 0:
        return (f"el job salió con error (código {rc}) — el problema está "
                "adentro del job, mirá su log")
    try:
        import signal
        nombre = signal.Signals(-rc).name
    except Exception:
        nombre = f"señal {-rc}"
    if -rc == 15:      # SIGTERM
        return (f"NO falló el job: lo MATARON con {nombre} desde afuera. Este "
                "arreglo lanza el job como hijo del proceso de la API, así que "
                "un `systemctl restart api.service` —o sea, un deploy— se lo "
                "lleva puesto a mitad de camino. Volvé a intentarlo sin "
                "deployar encima.")
    return (f"NO falló el job: lo mató {nombre} desde afuera (código {rc}). "
            "No es un error del código del job.")


def _lo_que_dijo(log, desde: int) -> str:
    """Lo que el job escribió en SU log durante ESTA corrida.

    User (2026-08-28), después de apretar REHACER y ver «corrió sin error y la
    tabla SIGUE sin el día»: *«cuando lo relanzamos tampoco dice el motivo ni
    nada»*.

    El primer intento de arreglarlo leyó `p.stdout` — y no traía nada, porque
    `run_job.sh` redirige todo al archivo. La explicación estaba ahí desde
    siempre; lo que faltaba era ir a buscarla donde está.

    Las últimas LÍNEAS y no los últimos N bytes: un corte por bytes parte un
    renglón al medio y lo que llega a la pantalla empieza en la mitad de una
    palabra.
    """
    try:
        with open(log, encoding="utf-8", errors="replace") as f:
            f.seek(desde)
            nuevo = f.read()
    except Exception as e:
        logger.warning("agente/rehacer: no pude leer %s (%s)", log, e)
        return ""
    lineas = [x.strip() for x in nuevo.splitlines() if x.strip()]
    return " · ".join(lineas[-3:])[:400]
