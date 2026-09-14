"""asistente/puerta.py — TODA herramienta pasa por acá ANTES de ejecutarse.

── SU ROL EN EL CICLO: es el paso 2½ ──

El modelo pide una herramienta y elige los argumentos. Entre ese pedido y la
ejecución hay un lugar donde el código puede mirar lo que eligió y **cortar**.
Eso es esta puerta. En Google ADK el gancho se llama `before_tool_callback`;
acá es una función, porque no hay framework.

    1. el modelo pide      cobros_futuros(cuenta="999", dias=90)
    2. ► LA PUERTA         ¿"999" está habilitada? → NO
    3. la herramienta      NO CORRE
    4. el modelo recibe    {"error": "la cuenta '999' no está habilitada", …}

⚠️⚠️ **POR QUÉ EXISTE, Y NO ES PROLIJIDAD: LA VALIDACIÓN SE OLVIDA.**

El alcance del asistente no puede depender de que el que escribe la herramienta
número seis se acuerde de chequear la cuenta. Se acuerda las primeras veces.
Y cuando se olvida **no falla nada**: la herramienta contesta, con números que
parecen buenos, sobre una cuenta que nadie habilitó.

Ya nos pasó de cerca: el test que congelaba «toda consulta lleva el filtro de
permiso» lee el SQL del archivo — y `tenencia_actual` no escribe SQL (llama a
un service), así que ese test la daba por buena sin mirar nada.

── CÓMO ESCALA: SE MIRA EL ARGUMENTO, NUNCA LA HERRAMIENTA ─────────────────

**Ningún control de acá nombra una herramienta.** `cuenta_habilitada` se
dispara con cualquier herramienta que reciba un argumento llamado `cuenta`, sin
saber cuál es ni qué hace. Por eso la herramienta #3 y la #8 quedan cubiertas
sin tocar este archivo — es la misma idea que hace que la ficha salga del
docstring y que `_tabla` la declare la herramienta.

Un control que empiece con `if nombre == "cobros_futuros"` es la señal de que
algo está en el lugar equivocado: eso va adentro de la herramienta.

── LA CONTRACARA, Y ESTÁ CUBIERTA POR UN TEST ──────────────────────────────

Si se mira el NOMBRE del argumento, una herramienta que llame `id_cuenta` a lo
mismo **pasa de largo en silencio** — el peor modo de falla que hay. Por eso
`test_ninguna_herramienta_le_pone_otro_nombre_a_la_cuenta` congela que el
parámetro se llame `cuenta` y nada más.

── ESTO NO REEMPLAZA LA VALIDACIÓN DE CADA HERRAMIENTA ─────────────────────

Las dos siguen, y no son «dos copias sin árbitro» (REGLA #9): las dos leen LA
MISMA fuente, `permitido.cuentas()`. Son dos lectores de un solo dato.

Y hacen falta las dos porque **no toda llamada pasa por el ciclo**: el diag
`scripts/diag_herramienta.py` corre la función directo. Si la validación viviera
sólo acá, ese script leería cualquier cuenta.

    la puerta         → cubre lo que el MODELO pide (y a la herramienta que se olvidó)
    cada herramienta  → cubre a quien la llame de afuera del ciclo
"""
from __future__ import annotations

import logging

from asistente import permitido

logger = logging.getLogger(__name__)


def cuenta_habilitada(nombre: str, args: dict) -> dict | None:
    """Ninguna herramienta corre sobre una cuenta que no esté habilitada.

    Fail-closed en los dos sentidos: si no hay NINGUNA cuenta declarada, no
    corre nada; y una cuenta que el modelo se inventó no llega a la base.

    No se mira qué herramienta es: se mira si recibió un argumento `cuenta`.
    """
    if "cuenta" not in args:
        return None
    if not permitido.cuentas():
        return permitido.como_error()
    pedida = str(args.get("cuenta") or "").strip()
    if pedida not in permitido.cuentas():
        logger.warning("asistente: %s pidió la cuenta %r, que no está habilitada",
                       nombre, pedida)
        return {
            "error": f"la cuenta {pedida!r} no está habilitada para el asistente",
            "cuentas_habilitadas": permitido.cuentas(),
            "que_hacer": ("Preguntale al usuario cuál de las cuentas habilitadas "
                          "quiere. NO contestes con otra cuenta."),
        }
    return None


# Los controles que corren, en orden. Sumar uno es agregarlo acá y nada más —
# igual que `control.CONTROLES` para la respuesta. El primero que corta, corta:
# devolverle al modelo tres errores a la vez no lo ayuda a arreglar ninguno.
CONTROLES = (cuenta_habilitada,)


def revisar(nombre: str, args: dict | None) -> dict | None:
    """`None` = que corra. Un dict = **NO corre**, y eso es lo que lee el modelo.

    ⚠️ NUNCA levanta. Un control que reviente no puede cortar la conversación:
    se loguea y se deja pasar, porque cada herramienta valida lo suyo igual. La
    puerta es la red, no el único piso.
    """
    if not isinstance(args, dict):
        return None
    for control in CONTROLES:
        try:
            if (corte := control(nombre, args)) is not None:
                return corte
        except Exception as e:
            logger.warning("asistente: el control %s reventó (%s) — dejo pasar",
                           control.__name__, e)
    return None
