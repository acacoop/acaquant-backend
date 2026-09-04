"""`agente/redactar.py` — **EL TEXTO DEL AVISO, ESCRITO CON LOS DATOS DELANTE.**

Doc: `docs/AGENT.md` §0.dn.

EL PROBLEMA
===========

De las 24 habilidades, **16 no tienen ningún arreglo**: todo lo que producen es
un aviso, y en un aviso el TEXTO es el entregable entero. Ese texto se escribía
a mano en el detector, así que era el mismo para todos los casos de esa regla:

    que_hacer="Nada: es el número del día. Si el salto de la semana no se
               explica con las cinco de arriba, mirar qué creció."

Esa frase le devuelve el trabajo al que lee: *«mirar qué creció»* es justo la
pregunta que la evidencia ya contesta —`top`, `bytes_hace_7d`, `bytes`— y que
nadie mira porque está en un JSON. Es el mismo defecto que el user ya marcó dos
veces sobre otra pantalla (*«es todo muy mecánico, no va»*, `api/services/
salud.py`) y sobre el `que_hacer` de los proveedores (`agente/tipos.py`: *«un
texto que no cambia con el caso no informa: entrena a saltearlo»*).

CÓMO SE EVITA QUE SEA BERRETA
=============================

Berreta no lo hace el modelo: lo hace pedirle que escriba sobre algo que no le
diste. Acá no escribe *sobre* el problema — **contesta la pregunta que el texto
fijo deja abierta, con la evidencia adelante**, y todo lo que dice se verifica
antes de mostrarse. Cinco decisiones, en orden de importancia:

1. **NO DECIDE NADA.** El detector ya dijo que hay un problema, cuál es la
   severidad y cuál es la evidencia. Esto sólo REDACTA. El invariante #1 (una
   corrida que no pudo mirar no cierra nada) no se toca ni de lejos: el modelo
   no participa de detectar, cerrar ni reabrir.
2. **EL PISO NO SE PISA.** `que_hacer` —el texto determinista— sigue en su
   columna, intacto. Lo del modelo vive en `ia_texto`, aparte. Si el gateway no
   contesta, si no hay presupuesto, si la validación rechaza: se muestra el
   piso. **Meter IA acá no puede agregar un modo de falla nuevo**, sólo puede
   mejorar el texto — y apagarlo es `AGENTE_REDACTA=0`, no un revert.
3. **SE VALIDA MECÁNICAMENTE, no «a ojo».** `VALIDADORES` es una cadena de
   funciones; la que importa es `_v_numeros`: **todo número del texto tiene que
   estar copiado de los hechos**. Si inventó uno, se descarta la respuesta
   entera. Un texto lindo con un número inventado es peor que la frase de molde.
4. **EL ALCANCE SE DERIVA, no se lista.** Se redacta lo que NO tiene arreglo.
   Una habilidad nueva sin botón entra sola; una con botón queda afuera sola
   (ahí el texto es el botón, y el modelo no aporta). No hay una lista de 16
   nombres que alguien se olvide de actualizar — eso es lo que la REGLA #10
   pide y lo que `test_solo_los_avisos_se_redactan` congela.
5. **UN SOLO PROMPT PARA LAS 16, Y PARA LA 25.** Lo que varía lo aporta el
   hallazgo (`problema`, `detalle`, `evidencia`) y el catálogo (`que_mira`, ya
   declarado en castellano por la REGLA #10). **Cero prompts por detector**: un
   prompt por habilidad sería la misma frase de molde de vuelta, escrita en
   otro archivo y pagándola.

QUÉ NO ESTÁ ACÁ
===============

SQL. Este módulo es puro: arma el pedido, llama al gateway y valida. Quien lee
los pendientes y quien escribe la columna es `agente/registro.py` —la puerta
única (invariante #5)— y quien decide *cuándo* correrlo es `jobs/agente.py`,
igual que el triage. Este archivo no sabe que existe un daemon.
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re

logger = logging.getLogger(__name__)

TAREA = "agente_texto"

# El techo del texto. Un aviso que no entra en dos renglones de la tab AHORA no
# se lee: compite con el `problema` y con el error crudo, que ya están arriba.
MAX_CHARS = 260
MIN_CHARS = 20
# Dos intentos por hallazgo y se abandona. Un hallazgo cuyo texto la validación
# rechaza siempre (evidencia pobre, por ejemplo) no puede pagarse en cada
# pasada del daemon: se queda con el piso, que para eso está.
MAX_INTENTOS = 2
# Techo de plata por pasada, declarado. Mismo criterio que `triage.TOPE_DIARIO`
# y que `vigencia.TOPE_POR_CORRIDA`: un tope que no está escrito no es un tope.
TOPE_POR_PASADA = 4

_MAX_EV = 2500          # recorte de la evidencia que viaja al modelo


def encendido() -> bool:
    """El kill switch. Apagarlo NO deja avisos sin texto: deja el piso."""
    return os.getenv("AGENTE_REDACTA", "1").strip().lower() not in (
        "0", "no", "false", "off")


def alcanza(habilidad: str, regla: str) -> bool:
    """**Se redacta lo que NO tiene arreglo.** Derivado, no listado.

    Un hallazgo con botón ya dice qué hacer: apretarlo. Ahí el modelo no puede
    agregar nada y sí puede contradecir al botón, que es peor que no estar.
    """
    from agente import catalogo
    h = catalogo.HABILIDADES.get(habilidad)
    return bool(h) and not h.arreglo_de(regla)


# ── EL PEDIDO ──────────────────────────────────────────────────────────────

_SYSTEM = f"""Sos el redactor de avisos del AV AGENT de TradingAV, una mesa de
capitales argentina (bonos, Primary/ROFEX, 1816, Aunesa, Postgres). Un detector
ya encontró algo y ya decidió que es un problema: vos NO decidís eso ni lo
discutís. Tu único trabajo es escribir la línea «qué hacer» que va debajo del
aviso, para alguien que dirige el producto y no programa.

Te dan los hechos. **Leé, no adivines.** Todo lo que escribas tiene que salir de
ahí: si algo no está, no lo sabés.

REGLAS DURAS (si no podés cumplirlas, contestá con `no_se` y `que_hacer` vacío):
- Máximo {MAX_CHARS} caracteres. Una o dos frases. Castellano rioplatense.
- **No escribas ningún número que no esté copiado tal cual de los hechos.** No
  calcules, no conviertas unidades, no redondees, no estimes.
- Nombrá lo concreto que aparece en la evidencia (la tabla, el ticker, el job,
  el proveedor). Si el aviso no requiere hacer nada, decilo y decí QUÉ MIRAR la
  próxima vez, con el nombre del dato.
- Prohibido: «revisar», «verificar», «monitorear», «parece que», «es posible
  que», «se recomienda», y cualquier frase que sirva igual para otro aviso.
- No repitas lo que ya dice «QUÉ ENCONTRÓ»: eso ya está arriba en la pantalla.
- Texto plano. Sin markdown, sin viñetas, sin comillas invertidas.

Contestá SOLO un JSON, sin markdown:
{{"que_hacer": "la línea, o cadena vacía si no podés",
  "no_se": "qué dato te falta para poder escribirla, o cadena vacía"}}
"""


def hechos(fila: dict) -> str:
    """El bloque que ve el modelo. **Es también contra lo que se valida.**

    Que el prompt y el validador lean el MISMO string no es una comodidad: es
    lo que hace que `_v_numeros` signifique algo. Si el validador comparara
    contra otra cosa —la evidencia sola, digamos— un número correcto que salió
    del `problema` se rechazaría, y uno inventado podría colarse.
    """
    ev = fila.get("evidencia")
    if isinstance(ev, str):
        try:
            ev = json.loads(ev)
        except Exception:
            ev = {}
    partes = [
        f"QUÉ MIRA ESTA HABILIDAD: {fila.get('que_mira') or '(no declarado)'}",
        f"SUJETO: {fila.get('sujeto', '')}",
        f"REGLA: {fila.get('regla', '')}",
        f"QUÉ ENCONTRÓ: {fila.get('problema', '')}",
    ]
    if fila.get("detalle"):
        partes.append(f"ERROR CRUDO, TAL CUAL: {fila['detalle']}")
    partes.append("EVIDENCIA (JSON): " + json.dumps(
        ev or {}, ensure_ascii=False, default=str)[:_MAX_EV])
    partes.append(f"TEXTO FIJO QUE HAY HOY (el piso, mejoralo o no lo toques): "
                  f"{fila.get('que_hacer', '')}")
    return "\n".join(partes)


# ── LA VALIDACIÓN ──────────────────────────────────────────────────────────
#
# Cada validador recibe (texto, hechos, fila) y devuelve el MOTIVO del rechazo
# o "" si pasa. Sumar una validación es una función y una línea en la tupla, y
# ninguna sabe de qué detector viene el hallazgo — por eso valen para las 16.

_NUM = re.compile(r"\d+")
_MARCAS = ("```", "**", "##", "`", "\n\n", '{"', '"}')
_MULETILLAS = (
    "parece que", "es posible", "podría ser", "podria ser", "se recomienda",
    "sería conveniente", "seria conveniente", "en caso de que", "revisar el",
    "verificar el", "verificar que", "monitorear", "hacer seguimiento",
    "estar atento", "prestar atención", "prestar atencion", "tener en cuenta",
    "no se puede determinar", "más información", "mas informacion",
)


def _v_largo(texto: str, _h: str, _f: dict) -> str:
    if len(texto) < MIN_CHARS:
        return f"muy corto ({len(texto)} caracteres)"
    if len(texto) > MAX_CHARS:
        return f"muy largo ({len(texto)} caracteres, tope {MAX_CHARS})"
    return ""


def _v_formato(texto: str, _h: str, _f: dict) -> str:
    for m in _MARCAS:
        if m in texto:
            return f"trae formato que la pantalla no dibuja: {m!r}"
    return ""


def _v_muletillas(texto: str, _h: str, _f: dict) -> str:
    bajo = texto.lower()
    for m in _MULETILLAS:
        if m in bajo:
            return f"muletilla «{m}»: sirve igual para cualquier otro aviso"
    return ""


def _v_numeros(texto: str, hechos_txt: str, _f: dict) -> str:
    """**La guarda que hace toda la diferencia.**

    Todo número del texto tiene que estar copiado de los hechos. Un aviso con
    un número inventado es estrictamente peor que la frase de molde: la frase
    de molde no informa, el número inventado desinforma — y encima con la
    autoridad de un dato.

    Es a propósito ESTRICTA (no acepta redondeos ni conversiones): el prompt le
    pide explícitamente que no calcule, y el costo de equivocarse para el otro
    lado es mostrarle a la mesa un número que no existe.
    """
    permitidos = set(_NUM.findall(hechos_txt))
    inventados = sorted({n for n in _NUM.findall(texto) if n not in permitidos})
    if inventados:
        return f"números que no están en la evidencia: {', '.join(inventados[:4])}"
    return ""


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9áéíóúñ ]+", " ", t.lower()).strip()


def _v_calco(texto: str, _h: str, fila: dict) -> str:
    """Ni repetir el problema (ya está arriba) ni devolver el piso tal cual."""
    for campo, como in (("problema", "repite lo que ya dice el problema"),
                        ("que_hacer", "es el texto fijo de vuelta")):
        otro = _norm(str(fila.get(campo) or ""))
        if otro and difflib.SequenceMatcher(
                None, _norm(texto), otro).ratio() > 0.85:
            return como
    return ""


VALIDADORES = (_v_largo, _v_formato, _v_muletillas, _v_numeros, _v_calco)


def revisar(texto: str, hechos_txt: str, fila: dict) -> str:
    """El motivo del PRIMER rechazo, o "" si pasa todo."""
    for v in VALIDADORES:
        if (motivo := v(texto, hechos_txt, fila)):
            return motivo
    return ""


# ── LA LLAMADA ─────────────────────────────────────────────────────────────

def _json_de(crudo: str) -> dict | None:
    t = (crudo or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-z]*\s*|\s*```$", "", t)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(t[i:j + 1])
    except Exception:
        return None
    return d if isinstance(d, dict) else None


def redactar_uno(fila: dict) -> dict:
    """`{texto, rechazo, traza}`. **Nunca levanta y nunca escribe.**

    `rechazo` no es ruido: se guarda en la fila y es lo que hace auditable la
    calidad. Sin él, «el modelo no escribió nada» y «el modelo escribió una
    macana y la tiré» se ven iguales — que es exactamente el error que este
    subsistema no puede cometer (invariante #1, aplicado a sí mismo).
    """
    if not encendido():
        return {"texto": "", "rechazo": "apagado (AGENTE_REDACTA=0)", "traza": None}
    if not alcanza(fila.get("habilidad", ""), fila.get("regla", "")):
        return {"texto": "", "rechazo": "tiene arreglo: el texto es el botón",
                "traza": None}

    bloque = hechos(fila)
    try:
        from core import ai
        crudo, traza = ai.completar_con_traza(
            TAREA, system=_SYSTEM, user=bloque,
            detalle=f"{fila.get('habilidad')}/{fila.get('regla')}/{fila.get('sujeto')}")
    except Exception as e:      # cinturón: el gateway ya promete no levantar
        logger.warning("redactar: fallo inesperado del gateway (%s)", e)
        return {"texto": "", "rechazo": f"gateway: {type(e).__name__}", "traza": None}

    if not crudo:
        return {"texto": "", "rechazo": "el gateway no contestó (sin key, "
                                        "presupuesto o fallo del proveedor)",
                "traza": traza}
    dato = _json_de(crudo)
    if dato is None:
        return {"texto": "", "rechazo": "no contestó un JSON", "traza": traza}

    texto = " ".join(str(dato.get("que_hacer") or "").split())
    no_se = str(dato.get("no_se") or "").strip()
    if not texto:
        return {"texto": "", "traza": traza,
                "rechazo": (f"dijo que no sabe: {no_se[:140]}" if no_se
                            else "contestó vacío y sin decir qué le faltaba")}
    if (motivo := revisar(texto, bloque, fila)):
        logger.info("redactar: rechazado %s/%s — %s | «%s»", fila.get("habilidad"),
                    fila.get("regla"), motivo, texto[:120])
        return {"texto": "", "rechazo": motivo, "traza": traza}
    return {"texto": texto, "rechazo": "", "traza": traza}
