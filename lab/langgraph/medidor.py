"""`lab/langgraph/medidor.py` — EL CONTADOR DE TOKENS. Doc: `core/ai.py`.

En la terminal daba igual: lo pagaba una corrida a mano y se veía en pantalla.
Adentro de la app **no**: sería gasto sin techo y sin registro de quién pidió
qué. Este módulo pone las dos cosas que ya tenías construidas alrededor de
LangChain, **sin tocar el grafo ni las herramientas**.

    ANTES de arrancar   ¿queda presupuesto del día?  →  si no, no se investiga
    en CADA llamada     una fila en `ia.trazas`      →  qué, quién, cuántos

⚠️ **EL PRESUPUESTO SE MIRA UNA VEZ, AL EMPEZAR, Y NO EN CADA LLAMADA.** No es
descuido: un callback de LangChain no puede abortar la conversación a mitad de
camino sin dejarla rota, y lo que acota el gasto de UNA investigación ya existe
y es duro — `MAX_PASOS` en el grafo. O sea: el techo del día lo pone el
presupuesto, el techo de una investigación lo pone el grafo.

⚠️ El medidor **nunca rompe la investigación**. Si la traza no se puede
escribir, se pierde la traza; que se caiga lo que estabas averiguando porque
falló el contador sería el peor negocio posible.
"""
from __future__ import annotations

import logging
import time

from langchain_core.callbacks import BaseCallbackHandler

TAREA = "investigador"

logger = logging.getLogger(__name__)


def hay_presupuesto(usuario: str = "") -> tuple[bool, str]:
    """(¿se puede?, por qué no). Se consulta ANTES de arrancar.

    Devuelve el MOTIVO y no un booleano pelado porque «se acabó tu cuota» y
    «se acabó la del sistema» se arreglan distinto, y el que lo lee en pantalla
    necesita saber cuál de las dos es.
    """
    try:
        from core import ai
        motivo = ai.motivo_presupuesto(usuario or None)
    except Exception as e:
        # Que no se pueda consultar el presupuesto NO bloquea: es control de
        # costos, no un gate de seguridad. Misma decisión que `core/ai.py`.
        logger.warning("medidor: no pude consultar el presupuesto (%s)", e)
        return True, ""
    if motivo is None:
        return True, ""
    return False, ("se agotó el presupuesto de tokens del día del sistema"
                   if motivo == "global" else
                   "se agotó tu presupuesto de tokens del día")


def _tokens(salida) -> tuple[int | None, int | None]:
    """Los tokens de una respuesta. **Dos formas, porque LangChain cambió.**

    En `llm_output["token_usage"]` (lo que devuelve el dialecto OpenAI) y en
    `usage_metadata` del mensaje (lo nuevo). Se miran las dos: quedarse con una
    hace que el contador diga cero sin fallar — y un contador en cero no se
    distingue de «no gastamos nada».
    """
    uso = (getattr(salida, "llm_output", None) or {}).get("token_usage") or {}
    if uso:
        return uso.get("prompt_tokens"), uso.get("completion_tokens")
    for tanda in (getattr(salida, "generations", None) or []):
        for g in tanda:
            meta = getattr(getattr(g, "message", None), "usage_metadata", None)
            if meta:
                return meta.get("input_tokens"), meta.get("output_tokens")
    return None, None


class Medidor(BaseCallbackHandler):
    """Se cuelga del modelo y anota cada llamada. El grafo ni se entera."""

    def __init__(self, modelo: str, usuario: str = "", detalle: str = ""):
        self.modelo, self.usuario, self.detalle = modelo, usuario, detalle
        self.llamadas = self.tokens_in = self.tokens_out = 0
        self._t0: dict = {}

    def on_chat_model_start(self, serialized, messages, *, run_id, **kw):
        self._t0[run_id] = time.monotonic()

    def on_llm_start(self, serialized, prompts, *, run_id, **kw):
        self._t0[run_id] = time.monotonic()

    def _ms(self, run_id) -> int:
        t0 = self._t0.pop(run_id, None)
        return int((time.monotonic() - t0) * 1000) if t0 else 0

    def on_llm_end(self, response, *, run_id, **kw):
        tin, tout = _tokens(response)
        self.llamadas += 1
        self.tokens_in += tin or 0
        self.tokens_out += tout or 0
        self._anotar(tin, tout, self._ms(run_id), True, None)

    def on_llm_error(self, error, *, run_id, **kw):
        self.llamadas += 1
        self._anotar(None, None, self._ms(run_id), False, str(error)[:400])

    def _anotar(self, tin, tout, ms, ok, error):
        try:
            from core import ai
            ai.registrar(TAREA, modelo=self.modelo, usuario=self.usuario or None,
                         tokens_in=tin, tokens_out=tout, latencia_ms=ms,
                         ok=ok, error=error, detalle=self.detalle)
        except Exception as e:
            logger.warning("medidor: no pude anotar la traza (%s)", e)

    def resumen(self) -> dict:
        return {"llamadas": self.llamadas, "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
                "tokens": self.tokens_in + self.tokens_out}
