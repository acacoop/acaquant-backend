"""asistente/control.py — REVISAR LO QUE CONTESTÓ EL MODELO, CON CÓDIGO.

── SU ROL EN EL CICLO: es lo último que pasa, y no cambia la respuesta ──

El ciclo termina, el modelo escribió su texto, y ANTES de devolverlo se lo
revisa acá. El veredicto viaja al lado de la respuesta; **no la reemplaza ni la
bloquea**.

⚠️ **POR QUÉ AVISA Y NO BLOQUEA.** Todavía no sabemos cuántas falsas alarmas da.
Si bloqueara, la primera falsa alarma te deja sin una respuesta que estaba bien
— peor que el problema que vino a resolver. Cuando tengamos semanas de uso y el
número de falsas alarmas sea cero, ahí se puede endurecer. Esa decisión pide un
dato que hoy no existe.

⚠️⚠️ **ESTE ARCHIVO NO SABE NADA DE LAS HERRAMIENTAS.** Ni cuáles hay, ni qué
devuelven, ni qué significan sus campos. Trabaja sobre TRES TEXTOS: lo que
contestó el modelo, todo lo que se le mandó, y la pregunta. Por eso una
herramienta que se agregue mañana queda cubierta sin escribir una línea — el
mismo principio que la ficha (sale de la función) y el achicado (sale del nombre
y los argumentos).

Qué NO puede ver: un número CORRECTO usado mal. Si dice «en septiembre cobrás
611,83» cuando 611,83 es el total de los 60 días, el número está y pasa limpio.
Eso pide entender la pregunta, y es el trabajo de un agente revisor — que va
después, y sólo si esto deja pasar algo que importe.
"""
from __future__ import annotations

import re

# ── LAS PERILLAS ────────────────────────────────────────────────────────────
#
# Enteros hasta acá se dejan pasar sin buscarlos. Son los que el modelo produce
# contando, no copiando: «6 pagos», «2 cuentas», «octubre» (10), un día del mes.
# Exigirle que estén en el JSON sería marcar como invento algo que está bien.
ENTEROS_LIBRES = 31

# Cuánto puede alejarse un número del que le dio origen para seguir contando
# como el mismo. Es para el REDONDEO: «unos 612» sobre 611,83, «unos 600» sobre
# 611,83.
#
# ⚠️ Es generoso a propósito, y hay que saber qué compra y qué paga: **esto caza
# INVENTOS, no errores de 2%.** Un número inventado no se parece al real — es
# otro. Apretar esta perilla no atraparía más inventos, sólo empezaría a marcar
# redondeos legítimos, y una alarma que suena por nada se deja de mirar en una
# semana.
TOLERANCIA_PCT = 0.02

# Debajo de esto, la comparación es absoluta y no porcentual: el 2% de un número
# chico es tan poco que cualquier redondeo caería afuera.
TOLERANCIA_MINIMA = 0.51

# Cuántos números sospechosos se nombran en el aviso. Si hay 40, el problema no
# es cuál: es que algo salió muy mal.
MAX_A_MOSTRAR = 8


# ── LEER NÚMEROS DE UN TEXTO ────────────────────────────────────────────────

# Un número puede venir con separadores de miles y decimales, y el modelo mezcla
# convenciones: 611,83 · 611.83 · 1.234,56 · 1,234.56 · 4500000
_RE_NUMERO = re.compile(r"-?\d[\d.,]*\d|-?\d")


def _lecturas(token: str) -> set[float]:
    """Las lecturas POSIBLES de un número escrito. Devuelve un conjunto porque
    el formato es ambiguo y no hay forma de resolverlo mirando el token solo:
    `1,234` es mil doscientos treinta y cuatro en inglés y uno coma doscientos
    treinta y cuatro en castellano.

    ⚠️ Se prueban las dos y alcanza con que UNA valide. Es deliberado: el error
    que queremos evitar es la falsa alarma. Un número inventado no valida bajo
    ninguna lectura — es otro número, no el mismo mal escrito.
    """
    t = token.strip().rstrip(".,")
    if not t:
        return set()
    out: set[float] = set()
    # Lectura 1: la coma es el decimal (castellano) → el punto es de miles.
    # Lectura 2: el punto es el decimal (inglés/JSON) → la coma es de miles.
    for quitar, decimal in ((".", ","), (",", ".")):
        limpio = t.replace(quitar, "").replace(decimal, ".")
        try:
            out.add(float(limpio))
        except ValueError:
            pass
    # Y la lectura donde todo es separador de miles: «1.234» = 1234.
    try:
        out.add(float(re.sub(r"[.,]", "", t)))
    except ValueError:
        pass
    return out


def numeros(texto: str) -> list[tuple[str, set[float]]]:
    """Los números de un texto, cada uno con sus lecturas posibles. Se conserva
    el token original porque es lo que se le muestra al usuario en el aviso: si
    dijera «611.83» cuando en pantalla se leía «611,83», no lo encontraría."""
    return [(m.group(0), _lecturas(m.group(0))) for m in _RE_NUMERO.finditer(texto or "")]


def _fundado(valores: set[float], fuente: set[float]) -> bool:
    """¿Alguna lectura de este número sale de la fuente, exacta o redondeada?"""
    for v in valores:
        if v in fuente:
            return True
        if abs(v) <= ENTEROS_LIBRES and float(v).is_integer():
            return True
        tol = max(abs(v) * TOLERANCIA_PCT, TOLERANCIA_MINIMA)
        if any(abs(v - f) <= tol for f in fuente):
            return True
    return False


# ── LOS CONTROLES ───────────────────────────────────────────────────────────

def numeros_fundados(respuesta: str, contexto: str, pregunta: str) -> list[dict]:
    """Todo número de la respuesta tiene que salir de algún lado.

    Tres fuentes valen, y las tres son legítimas:
      · lo que devolvieron las herramientas (y todo lo que se le mandó al modelo,
        que incluye los resultados de preguntas anteriores);
      · la propia pregunta del usuario («en 60 días» → 60);
      · contar y redondear (ver `ENTEROS_LIBRES` y `TOLERANCIA_PCT`).

    Lo que no sale de ninguna, el modelo lo puso él. Y en esta app un número
    puesto por el modelo es plata que no existe.
    """
    fuente: set[float] = set()
    for _tok, vals in numeros(contexto) + numeros(pregunta):
        fuente |= vals
    sueltos = [tok for tok, vals in numeros(respuesta) if not _fundado(vals, fuente)]
    if not sueltos:
        return []
    return [{
        "control": "numeros_fundados",
        "que_paso": "hay números en la respuesta que no están en lo que se le dio",
        "detalle": sueltos[:MAX_A_MOSTRAR],
        "cuantos": len(sueltos),
    }]


# ⚠️ **EL REGISTRO.** Sumar un control es agregarlo acá y nada más — misma firma
# para todos: `(respuesta, contexto, pregunta) -> list[dict]`.
#
# Los que vienen cuando el asistente crezca, y por qué todavía no están:
#   · `tickers_fundados` — mismo criterio con símbolos. Hoy el prompt le pide
#     que no invente tickers; esto lo verificaría en vez de pedirlo.
#   · `cuentas_fundadas` — que no nombre una cuenta fuera de las que miró. Ése
#     no es de calidad, es de seguridad.
#   · `cito_la_frescura` — que diga de cuándo son los datos cuando son viejos.
CONTROLES = (numeros_fundados,)


def revisar(respuesta: str | None, *, contexto: str, pregunta: str) -> dict:
    """Pasa la respuesta por todos los controles. NUNCA levanta.

    ⚠️ Un control que se rompe no puede tirar abajo una respuesta que ya está
    escrita: se anota el fallo del control y se sigue. Revisar no puede ser más
    frágil que lo revisado.
    """
    if not respuesta:
        return {"ok": True, "hallazgos": []}
    hallazgos: list[dict] = []
    for control in CONTROLES:
        try:
            hallazgos += control(respuesta, contexto, pregunta)
        except Exception as e:
            hallazgos.append({
                "control": getattr(control, "__name__", "?"),
                "que_paso": f"el control falló: {type(e).__name__}: {e}",
                "detalle": [], "cuantos": 0,
            })
    return {"ok": not hallazgos, "hallazgos": hallazgos}
