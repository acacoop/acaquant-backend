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

# Cuánto se espera al `run_job.sh`. El wrapper ya tiene su propio timeout (el
# segundo argumento); este es el del lado nuestro, un poco más largo para no
# cortar antes que él y quedarnos sin su mensaje.
ESPERA_S = 30 * 60


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
        "rompe": ("sin esto el AuM, la Tenencia Valorizada y Títulos en Alquiler "
                  "se quedan con el día anterior"),
    },
}


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
    (`diag_tenencia_fechas`, 2026-08-22): todos los viernes históricos están
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
    if not r["ok"]:
        return {**r, "corrio": True, "ya_estaba": False}

    # ⚠️ **LA PRUEBA ES LA TABLA, NO EL EXIT CODE.** Un job puede salir 0 y no
    # haber escrito una fila (la API devolvió vacío, el filtro dejó todo afuera).
    despues = hay_dato(job, fecha)
    if despues is None:
        return {"ok": False, "corrio": True,
                "error": "corrió pero no pude verificar si escribió"}
    if not despues:
        return {"ok": False, "corrio": True, "escribio": False,
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
    try:
        p = subprocess.run(
            [RUN_JOB, cfg["label"], cfg["timeout"], cfg["comando"]],
            capture_output=True, text=True, timeout=ESPERA_S)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"pasó de {ESPERA_S // 60} min y lo corté"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    salida = ((p.stdout or "") + (p.stderr or "")).strip()[-600:]
    if p.returncode != 0:
        return {"ok": False, "error": f"salió con código {p.returncode}",
                "salida": salida}
    return {"ok": True, "salida": salida}
