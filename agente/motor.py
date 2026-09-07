"""`agente/motor.py` — **LA AGENDA.** Un solo reloj. Doc: `AGENT.md` §2.

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

# ⚠️⚠️ **LA PASADA A PEDIDO TIENE OTRO PRESUPUESTO, Y NO ES UNA PREFERENCIA**
# (§0.eb). Del otro lado hay un transporte con su propio corte: el proxy de
# Vercel (`src/app/api/agente/[...path]/route.ts`) tiene `maxDuration = 30`.
#
# Mientras el botón corría lo mismo que el daemon esto no se notaba: casi nunca
# le tocaba a nadie y la pasada volvía en un segundo. Al forzar el ritmo (§0.dz)
# pasó a correr las ~27 habilidades de una, se fue muy arriba de 30 s, y el
# proxy cortó: **«no pude correr la pasada»**. Arreglar un botón mudo y dejarlo
# roto es peor que no tocarlo.
#
# 20 s deja margen para el viaje de ida y vuelta. Lo que no entra no se pierde:
# la pasada devuelve cuántas quedaron y la pantalla invita a apretar de nuevo —
# el mismo criterio que el presupuesto del daemon, que corta y sigue mañana.
PRESUPUESTO_PEDIDO_S = 20

# El ritmo del daemon. Vive ACÁ y no en `jobs/agente.py` porque lo necesita el
# LATIDO para decir cuándo vuelve — y `agente/` no puede importar a `jobs/`.
#
# En rueda mira seguido (los precios cambian); fuera de rueda afloja, porque de
# noche lo único que sigue teniendo sentido son los jobs y las tablas. Bajar el
# ritmo no es ahorro: es no llenar el log de nada 2.880 veces por noche.
CICLO_RUEDA_S, CICLO_QUIETO_S = 30, 300


def _fuera_de_ventana(fila: dict, ahora) -> str:
    """Por qué esta habilidad NO puede correr AHORA. `""` = puede.

    ⚠️ **La ventana y el ritmo son dos frenos distintos, y confundirlos hizo que
    el botón «MIRAR AHORA» no hiciera nada** (§0.dz). El RITMO es una decisión de
    frecuencia —«con cada 2 h alcanza»— y una persona que aprieta un botón la
    está anulando a propósito. La VENTANA es una condición del MUNDO: fuera de
    rueda `bono_sin_precio` vería todos los precios viejos y cantaría cien
    problemas que no existen. El ritmo se puede forzar; la ventana no.
    """
    ventana = fila.get("ventana") or "siempre"
    if ventana == "rueda" and not reloj.en_rueda(ahora):
        return "la rueda está cerrada"
    # ⚠️ `cierre` corre UNA vez por día hábil, con la rueda ya cerrada. Es la
    # ventana de lo que no se le puede seguir pidiendo al mercado.
    if ventana == "cierre" and not reloj.en_cierre(ahora):
        return "corre una vez por día, con la rueda ya cerrada"
    if ventana == "habil" and not reloj.dia_habil(ahora):
        return "hoy no es día hábil"
    return ""


def _le_toca(fila: dict, ahora, *, forzar: bool = False) -> bool:
    """¿Venció su ritmo y estamos en su ventana?

    `forzar` anula el RITMO —lo pidió una persona— y nunca la ventana.
    """
    if not fila.get("activa"):
        return False
    if _fuera_de_ventana(fila, ahora):
        return False
    if forzar:
        return True
    ult = fila.get("ultima_corrida_at")
    if ult is None:
        return True
    return (ahora - ult).total_seconds() >= float(fila["cada_segundos"])


def _agenda(ahora, *, forzar: bool = False) -> tuple[list[dict], list[dict]]:
    """A quién le toca, y **quién quedó afuera por la ventana y por qué**.

    Lo segundo no estaba y por eso una pasada podía no correr nada sin decir
    nada: el botón parecía roto cuando en realidad no había nadie a quien le
    tocara (§0.dz).
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT nombre, cada_segundos, ventana, activa, "
                    "       ultima_corrida_at FROM agente.habilidades")
        filas = [{"nombre": r[0], "cada_segundos": r[1], "ventana": r[2],
                  "activa": r[3], "ultima_corrida_at": r[4]}
                 for r in cur.fetchall()]
    fuera = [{"nombre": f["nombre"], "motivo": m} for f in filas
             if f.get("activa") and (m := _fuera_de_ventana(f, ahora))]
    # De la más rápida a la más lenta: lo que corre cada 2 minutos existe para
    # enterarse ahora, y hacerlo esperar detrás de un censo lo vuelve inútil.
    return (sorted((f for f in filas if _le_toca(f, ahora, forzar=forzar)),
                   key=lambda f: f["cada_segundos"]),
            sorted(fuera, key=lambda f: f["nombre"]))


def correr_una(nombre: str) -> dict:
    """Corre UNA habilidad y escribe por la puerta única.

    **Nunca levanta.** Un detector que revienta no puede tirar abajo la pasada:
    su fila queda en `error` y las demás siguen.
    """
    h = catalogo.HABILIDADES.get(nombre)
    if h is None:
        return {"ok": False, "error": f"«{nombre}» no está en el catálogo"}

    t0 = time.monotonic()
    traceback_txt = ""
    try:
        hallazgos = list(h.correr(catalogo.umbrales_de(nombre)) or [])
        resultado, error = tipos.OK, ""
    except tipos.SinDatos as e:
        # «No pude mirar». NO es una lista vacía: no se cierra nada.
        hallazgos, resultado, error = [], tipos.SIN_DATOS, str(e)[:400]
        logger.info("agente/%s: sin datos — %s", nombre, error)
    except Exception as e:
        import traceback as _tb
        hallazgos, resultado, error = [], tipos.ERROR, f"{type(e).__name__}: {e}"[:400]
        # El traceback entero queda en la fila (§0.dh): «KeyError: 'x'» a
        # secas no dice dónde, y «explicámelo» necesita el dónde.
        traceback_txt = "\n".join(_tb.format_exc().splitlines()[-40:])
        logger.exception("agente/%s: reventó", nombre)

    ms = int((time.monotonic() - t0) * 1000)
    try:
        r = registro.guardar(nombre, hallazgos, resultado=resultado,
                             error=error, duracion_ms=ms, traceback=traceback_txt)
    except Exception as e:
        logger.exception("agente/%s: no pude escribir lo que encontré", nombre)
        return {"ok": False, "habilidad": nombre, "error": str(e)[:300]}
    return {"habilidad": nombre, "ms": ms, **r}


def tick(*, forzar: bool = False, presupuesto_s: float | None = None) -> dict:
    """UNA pasada. Es lo único que llama el daemon.

    `forzar=True` es **una persona apretando el botón**: corre todas las que la
    ventana permite, sin esperar a que venza su ritmo. El daemon nunca lo usa.

    `fuentes.refrescar()` al empezar: todos los detectores de esta pasada ven
    **la misma foto**. En el agente viejo cada reloj leía lo suyo por su cuenta,
    en su momento, y sus veredictos se dibujaban uno al lado del otro como si
    hablaran del mismo instante.
    """
    ahora = reloj.ahora_utc()
    fuentes.refrescar()
    pendientes, fuera = _agenda(ahora, forzar=forzar)
    tope = PRESUPUESTO_S if presupuesto_s is None else float(presupuesto_s)
    corridas, t0 = [], time.monotonic()
    for f in pendientes:
        corridas.append(correr_una(f["nombre"]))
        if time.monotonic() - t0 > tope:
            logger.info("agente: corté la pasada por presupuesto (%d de %d) — "
                        "las que faltan van en la próxima", len(corridas),
                        len(pendientes))
            break
    return {
        "at": ahora.isoformat(), "en_rueda": reloj.en_rueda(ahora),
        "habil": reloj.dia_habil(ahora),
        "corridas": corridas, "pendientes": len(pendientes),
        "forzada": forzar, "presupuesto_s": tope,
        # LO QUE NO ENTRÓ EN EL PRESUPUESTO. No es lo mismo que
        # `fuera_de_ventana`: esto SÍ le tocaba y se corta por tiempo, así que
        # apretar de nuevo lo corre. Sin el número, una pasada cortada y una
        # completa se ven idénticas.
        "faltaron": max(0, len(pendientes) - len(corridas)),
        # QUIÉN NO CORRIÓ Y POR QUÉ. Sin esto, «no pasó nada» y «no había nada
        # que hacer» se ven exactamente igual.
        "fuera_de_ventana": fuera,
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

    # ⚠️ **EL LATIDO DICE CUÁNDO VUELVE.** Sin eso, quien lo lee tiene que
    # ADIVINAR cada cuánto late — y el umbral fijo de 180 s daba «detenido»
    # todas las noches, porque fuera de rueda el ciclo es de 300 s. Un círculo
    # que está en rojo cuando todo está bien enseña a ignorar el círculo.
    proximo = CICLO_RUEDA_S if reloj.en_rueda() else CICLO_QUIETO_S
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agente.latido (id, at, detalle, proximo_en_s) "
                "VALUES (1, now(), %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET at = now(), "
                "  detalle = EXCLUDED.detalle, proximo_en_s = EXCLUDED.proximo_en_s",
                (json.dumps(resultado, default=str), proximo))
    except Exception as e:
        logger.warning("agente: no pude latir (%s)", e)


def vivo() -> dict:
    """¿Está prendido? El círculo se apaga solo cuando el latido envejece — sin
    que nadie tenga que acordarse de apagarlo."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT at, detalle, "
                        "  EXTRACT(epoch FROM now() - at)::int, "
                        "  coalesce(proximo_en_s, 300) "
                        "FROM agente.latido WHERE id = 1")
            f = cur.fetchone()
    except Exception as e:
        return {"vivo": False, "error": str(e)[:200]}
    if not f:
        return {"vivo": False, "hace_s": None, "cada_s": None}
    hace, cada = int(f[2] or 0), int(f[3] or 300)
    # Tres ciclos de gracia: uno perdido es una pasada larga, tres es que se
    # murió. El umbral SALE del ritmo declarado, no de un número puesto a mano.
    return {"vivo": hace < cada * 3, "at": f[0].isoformat(), "hace_s": hace,
            "cada_s": cada, "detalle": dict(f[1] or {})}
