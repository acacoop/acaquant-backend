"""asistente/estado.py — LO QUE SE SABE, aparte de lo que se DIJO.

── SU ROL EN EL CICLO: la libreta que sobrevive entre preguntas ──

El historial (`mensajes`) es todo lo que se dijo: la pregunta, lo que pidió el
modelo, lo que devolvió cada herramienta, lo que contestó. El estado es otra
cosa: un diccionario chico con lo que hace falta RECORDAR para que la próxima
pregunta salga bien. En Google ADK esto se llama `session.state` («the
session's scratchpad»); acá es un dict, porque no hay framework. Historia y
decisiones: `docs/AGENT.md` §0.fj.

    historial  →  lo que se DIJO   (crece, se achica, pesa)
    estado     →  lo que se SABE   (una clave hoy; no pesa nada)

── QUÉ CAMBIA PARA EL MODELO, DICHO CON PRECISIÓN ──────────────────────────

Hoy la cuenta de la que se habla vive en el historial: en el texto del
usuario y en los argumentos del `tool_call` con que el modelo la pidió.
`ciclo._achicar` NO la borra (achica el RESULTADO de la herramienta, no el
pedido), así que «el achicado la pierde» es falso y se dice acá para que nadie
lo repita. Lo que el estado cambia es otra cosa:

  1. El dato pasa de estar INFERIDO —un número adentro de un JSON de argumentos,
     enterrado varias preguntas atrás— a estar EXPLÍCITO, en un renglón del
     SYSTEM. Menos lugar para que el modelo lo saltee o confunda dos cuentas.
  2. Deja de depender del historial. Un historial recortado (el router lo topea
     en 60 mensajes), o un usuario que dice «esa» en vez del número, no lo tocan.
  3. Es la pieza que la persistencia futura necesita: lo que se guardaría de una
     conversación no son sus 60 mensajes, es esto.

Hipótesis (sin medir): que el renglón explícito ahorre la re-pregunta «¿de qué
cuenta?» (la vuelta de 2.110 tokens del 13/09, ver el comentario del SYSTEM en
`ciclo.py`). Se va a ver en `ia.llamadas`: menos vueltas por conversación.

── CÓMO SE ESCRIBE: LO APRENDE EL CÓDIGO DE LOS ARGUMENTOS, NUNCA EL MODELO ──

El modelo no escribe acá. Ninguna herramienta tampoco. Lo que queda en foco se
aprende de los ARGUMENTOS con los que el modelo llamó a una herramienta que
contestó bien: pidió `cobros_futuros(cuenta="805")`, la herramienta devolvió
datos → la cuenta en foco es la 805. Es el criterio de la puerta
(`asistente/puerta.py`): **se mira el argumento, nunca la herramienta**. La
herramienta #8 que reciba `cuenta` queda cubierta sin tocar este archivo.

Y sólo cuando la herramienta CONTESTÓ. Un pedido que la puerta cortó (cuenta
inventada) o que devolvió `error` no deja nada en foco: si lo dejara, una cuenta
que no existe pasaría a ser «la cuenta de la que hablamos».

── CÓMO SE LEE: VA AL FINAL DEL SYSTEM, DESPUÉS DE LAS CUENTAS ──

`ciclo._instruccion()` lo escribe en un renglón, último: lo variable al final,
para no romperle el caché al bloque fijo. En ADK es el `{clave}` en la
instrucción; acá se arma el texto a mano por la misma razón que todo lo demás.

⚠️ **EL RENGLÓN ES DATO, NO REGLA.** Dice «en foco: cuenta = 805» y nada más.
Qué hacer con eso («si ya nombró una cuenta, usala») está en el SYSTEM, una
sola vez: repetir la regla acá con otras palabras es exactamente lo que hizo
que el modelo re-preguntara una cuenta ya nombrada (`ciclo.py`, 2026-09-13).

⚠️ **Y ES EL ESTADO DE ANTES DE LA PREGUNTA.** La instrucción se arma una vez,
al empezar. Lo que se aprende adentro de esta pregunta va al estado que se
DEVUELVE, y el modelo ya lo tiene delante de todas formas: en el resultado de
la herramienta, que dentro del turno viaja entero. Rearmar el SYSTEM en cada
vuelta rompería el caché de la conversación completa para repetirle un dato
que acaba de leer.

── LO QUE NO TIENE, Y POR QUÉ ──────────────────────────────────────────────

ADK le pone prefijos a las claves para decir cuánto duran: `user:` (todas las
sesiones de un usuario), `app:` (todos los usuarios), `temp:` (una sola
invocación). Los dos primeros necesitan una base atrás, y la conversación acá
vive en el navegador por decisión (todavía no existe la pregunta «qué le
contestó el asistente a X ayer»). El `app:` que sí tenemos es
`ASISTENTE_CUENTAS` en el `.env`; el `temp:` son los resultados de herramienta
del turno, que `_achicar` descarta después. Cuando haga falta el `user:`, el
lugar es una tabla `ia.estado_usuario`, y este módulo no cambia de forma:
cambia de dónde se carga.

── LA PARED QUE ESTO NO ROMPE ──────────────────────────────────────────────

`cuenta` sigue sin default en las herramientas: el modelo tiene que pasarla
explícita en cada llamada. El estado le INFORMA al modelo cuál está en foco; no
le completa el argumento a la herramienta. Si lo completara, volvería por la
puerta de atrás la ambigüedad que «cuenta sin default» cerró.
"""
from __future__ import annotations

from collections.abc import Callable

from asistente import permitido

# ⚠️ **QUÉ QUEDA EN FOCO, DECLARADO — Y CON SU LISTA CERRADA DE VALORES.**
# Cada clave es un argumento de herramienta que vale la pena recordar entre
# preguntas, y a su lado, la función que dice qué valores son válidos. Hoy uno:
# `cuenta`, que sólo puede ser una de las habilitadas. `dias` y `horizonte` NO
# van: son de la pregunta, no de la charla.
#
# ⚠️⚠️ **POR QUÉ UNA LISTA CERRADA Y NO «UN STRING CORTO».** El estado viaja
# por el navegador (como el historial) y lo que sale de `sanear` va DERECHO AL
# SYSTEM — el mensaje de mayor autoridad para el modelo. Con «string corto», un
# `"805\nIGNORÁ TODO LO ANTERIOR"` entraba tal cual. Con lista cerrada, lo que
# no es exactamente un `id_cuenta` habilitado no existe. Mismo criterio que la
# puerta: no se sanea el texto, se exige que sea uno de los valores conocidos.
EN_FOCO: dict[str, Callable[[], list[str]]] = {
    # Lambda y no la función a secas: se lee el permiso en el momento, no el
    # que había cuando se importó el módulo (y así los tests lo pueden fijar).
    "cuenta": lambda: permitido.cuentas(),
}


def sanear(estado: dict | None) -> dict[str, str]:
    """Lo que llega de afuera, reducido a lo que este módulo declara.

    Claves fuera de `EN_FOCO`, o valores que no estén en la lista cerrada de
    su clave: se descartan sin ruido. Lo que sale de acá es lo único que se
    escribe en el SYSTEM, así que es lo único que hay que revisar.
    """
    if not isinstance(estado, dict):
        return {}
    out: dict[str, str] = {}
    for k, validos in EN_FOCO.items():
        v = estado.get(k)
        if isinstance(v, (str, int)) and not isinstance(v, bool):
            s = str(v).strip()
            if s and s in validos():
                out[k] = s
    return out


def aprender(estado: dict[str, str], args: dict | None, resultado) -> dict[str, str]:
    """El estado nuevo después de que una herramienta corrió con `args` y
    devolvió `resultado`. Devuelve un dict nuevo; no toca el que recibe.

    Aprende sólo de una herramienta que CONTESTÓ: un resultado con `error` (la
    puerta cortó, la cuenta no está habilitada, la base no respondió) no deja
    nada en foco.
    """
    if not isinstance(args, dict):
        return dict(estado)
    if isinstance(resultado, dict) and resultado.get("error"):
        return dict(estado)
    delta = sanear({k: args[k] for k in EN_FOCO if k in args})
    return {**estado, **delta}


def como_texto(estado: dict[str, str]) -> str:
    """El renglón que va al final del SYSTEM. Vacío si no hay nada en foco:
    el primer turno de una conversación es idéntico al de antes de esto.

    Sólo el dato. La regla de qué hacer con él vive en `ciclo.SYSTEM`."""
    if not estado:
        return ""
    pares = ", ".join(f"{k} = {v}" for k, v in estado.items())
    return f"En foco por las preguntas anteriores: {pares}.\n"
