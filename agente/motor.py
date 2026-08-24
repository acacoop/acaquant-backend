"""`agente/motor.py` — **LA AGENDA.** Un solo reloj. Doc: `AGENT_2.0.md` §2.

El agente viejo tenía CUATRO programas separados haciendo exactamente lo mismo
—despertarse, mirar, anotar— y lo único que los diferenciaba era el ritmo. Eso
traía dos problemas que se veían en pantalla todos los días:

  · si se caía uno, los otros tres seguían mostrando datos frescos. **La
    pantalla se veía viva con un cuarto del agente muerto**, que es peor que
    estar caído entero.
  · cada reloj llevaba su propio horario y la pantalla los mezclaba: mostraba
    algo de hace 10 minutos al lado de algo de hace 4 horas sin decir cuál era
    cuál.

Acá hay UN agente. Cada habilidad declara su ritmo y su ventana; el motor
pregunta a quién le toca y lo corre.

LA PREGUNTA QUE SE CONTESTA UNA SOLA VEZ
========================================

*¿Qué hago si no puedo mirar?* Es la más importante del sistema, porque «no
encontré nada» y «no pude mirar» se ven iguales en la pantalla, y uno significa
que está todo bien y el otro que estás ciego. En el agente viejo la contestaban
los 19 detectores por su cuenta, cada uno distinto. Acá:

    devolvió        → `ok`        · lo que no vino se cierra POR AUSENCIA
    levantó SinDatos→ `sin_datos` · **no se cierra nada**
    reventó         → `error`     · **no se cierra nada**
    no le tocó      → la fila del catálogo lo dice sola

QUE UNA LENTA NO TAPE A UNA RÁPIDA
==================================

Cada pasada corre las habilidades que vencieron **en orden de ritmo**, de la más
rápida a la más lenta, y a las lentas les pone un presupuesto: si `soberanos_
faltantes` tarda dos minutos, el monitor de precios no puede quedarse esperando.
Es una decisión tomada a propósito y no algo que se descubre en producción.
"""
from __future__ import annotations

import logging
import time

from agente import catalogo, fuentes, registro, reloj, tipos
from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Cuánto puede durar UNA pasada antes de que el motor corte y siga en la
# siguiente. Lo caro (el censo de 1816) entra igual: lo que se protege es que la
# pasada VUELVA, no que termine todo.
PRESUPUESTO_S = 240


def _le_toca(fila: dict, ahora) -> bool:
    """¿Venció su ritmo y estamos en su ventana?"""
    if not fila.get("activa"):
        return False
    ventana = fila.get("ventana") or "siempre"
    if ventana == "rueda" and not reloj.en_rueda(ahora):
        return False
    if ventana == "habil" and not reloj.dia_habil(ahora):
        return False
    ult = fila.get("ultima_corrida_at")
    if ult is None:
        return True
    return (ahora - ult).total_seconds() >= float(fila["cada_segundos"])


def _agenda(ahora) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT nombre, cada_segundos, ventana, activa, "
                    "       ultima_corrida_at FROM agente.habilidades")
        filas = [{"nombre": r[0], "cada_segundos": r[1], "ventana": r[2],
                  "activa": r[3], "ultima_corrida_at": r[4]}
                 for r in cur.fetchall()]
    # De la más rápida a la más lenta: lo que corre cada 2 minutos existe para
    # enterarse ahora, y hacerlo esperar detrás de un censo lo vuelve inútil.
    return sorted((f for f in filas if _le_toca(f, ahora)),
                  key=lambda f: f["cada_segundos"])


def correr_una(nombre: str) -> dict:
    """Corre UNA habilidad y escribe por la puerta única.

    **Nunca levanta.** Un detector que revienta no puede tirar abajo la pasada:
    su fila queda en `error` y las demás siguen.
    """
    h = catalogo.HABILIDADES.get(nombre)
    if h is None:
        return {"ok": False, "error": f"«{nombre}» no está en el catálogo"}

    t0 = time.monotonic()
    try:
        hallazgos = list(h.correr(catalogo.umbrales_de(nombre)) or [])
        resultado, error = tipos.OK, ""
    except tipos.SinDatos as e:
        # «No pude mirar». NO es una lista vacía: no se cierra nada.
        hallazgos, resultado, error = [], tipos.SIN_DATOS, str(e)[:400]
        logger.info("agente/%s: sin datos — %s", nombre, error)
    except Exception as e:
        hallazgos, resultado, error = [], tipos.ERROR, f"{type(e).__name__}: {e}"[:400]
        logger.exception("agente/%s: reventó", nombre)

    ms = int((time.monotonic() - t0) * 1000)
    try:
        r = registro.guardar(nombre, hallazgos, resultado=resultado,
                             error=error, duracion_ms=ms)
    except Exception as e:
        logger.exception("agente/%s: no pude escribir lo que encontré", nombre)
        return {"ok": False, "habilidad": nombre, "error": str(e)[:300]}
    return {"habilidad": nombre, "ms": ms, **r}


def tick() -> dict:
    """UNA pasada. Es lo único que llama el daemon.

    `fuentes.refrescar()` al empezar: todos los detectores de esta pasada ven
    **la misma foto**. En el agente viejo cada reloj leía lo suyo por su cuenta,
    en su momento, y sus veredictos se dibujaban uno al lado del otro como si
    hablaran del mismo instante.
    """
    ahora = reloj.ahora_utc()
    fuentes.refrescar()
    pendientes = _agenda(ahora)
    corridas, t0 = [], time.monotonic()
    for f in pendientes:
        corridas.append(correr_una(f["nombre"]))
        if time.monotonic() - t0 > PRESUPUESTO_S:
            logger.info("agente: corté la pasada por presupuesto (%d de %d) — "
                        "las que faltan van en la próxima", len(corridas),
                        len(pendientes))
            break
    return {
        "at": ahora.isoformat(), "en_rueda": reloj.en_rueda(ahora),
        "habil": reloj.dia_habil(ahora),
        "corridas": corridas, "pendientes": len(pendientes),
        "ms": int((time.monotonic() - t0) * 1000),
        "nuevos": sum(int(c.get("nuevos") or 0) for c in corridas),
        "reincidencias": sum(int(c.get("reincidencias") or 0) for c in corridas),
    }


def latir(resultado: dict) -> None:
    """El LATIDO: una fila que dice que el agente está vivo y cuándo miró.

    Es lo que sostiene el círculo verde. Un cron no puede sostener esa
    afirmación: entre corrida y corrida no hay nadie y «prendido» sería una
    frase sobre el pasado.
    """
    import json
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agente.latido (id, at, detalle) VALUES (1, now(), %s) "
                "ON CONFLICT (id) DO UPDATE SET at = now(), detalle = EXCLUDED.detalle",
                (json.dumps(resultado, default=str),))
    except Exception as e:
        logger.warning("agente: no pude latir (%s)", e)


def vivo() -> dict:
    """¿Está prendido? El círculo se apaga solo cuando el latido envejece — sin
    que nadie tenga que acordarse de apagarlo."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT at, detalle, "
                        "  EXTRACT(epoch FROM now() - at)::int "
                        "FROM agente.latido WHERE id = 1")
            f = cur.fetchone()
    except Exception as e:
        return {"vivo": False, "error": str(e)[:200]}
    if not f:
        return {"vivo": False, "hace_s": None}
    hace = int(f[2] or 0)
    return {"vivo": hace < 180, "at": f[0].isoformat(), "hace_s": hace,
            "detalle": dict(f[1] or {})}
