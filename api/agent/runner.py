"""Runner del tool-use loop (provider-agnostic).

Flujo:
1. Recibimos el mensaje del usuario + history.
2. `router.decide_model()` elige haiku o sonnet para este turno.
3. `get_provider()` nos devuelve el cliente (Claude o Gemini según LLM_PROVIDER).
4. Armamos el system prompt dinámico (framework + context + data inventory on demand).
5. Loop: modelo decide → si text final, respondemos; si tool_use, ejecutamos
   la tool y volvemos al modelo con el resultado.
6. Corte por modelo (MAX_STEPS_BY_ALIAS) para controlar costo.

El history que entra/sale está en formato CANÓNICO (estilo Claude):
  [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": [{"type": "text", ...} | {"type": "tool_use", ...}]},
    {"role": "user", "content": [{"type": "tool_result", ...}]},
    ...
  ]

Si el history llega en formato Gemini viejo (items con `parts`), lo descartamos
silenciosamente — el usuario arranca la conversación de cero esa vez.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from api.agent.context import build_market_context
from api.agent.data_inventory import build_data_inventory
from api.agent.prompt import build_system_prompt
from api.agent.provider import (
    LLMError,
    LLMProvider,
    get_provider,
)
from api.agent.router import decide_model
from api.agent.tools import dispatch
from api.agent.types import LLMResponse, tool_result_message, user_text_message

logger = logging.getLogger(__name__)

# Tope de turnos encadenados, por alias de modelo. Sonnet maneja análisis
# multi-paso bien (puede necesitar 6-7 tools en una vista de mercado completa);
# Haiku conviene cortarlo antes (lookups simples no deberían encadenar mucho).
# Si el alias del modelo no está en el dict, se usa MAX_STEPS_DEFAULT.
MAX_STEPS_DEFAULT = 6
MAX_STEPS_BY_ALIAS = {"sonnet": 8, "haiku": 4}

# Tope de turnos de USUARIO que se mandan al modelo. El frontend sigue viendo
# la conversación entera; sólo se trunca lo que va a la API para evitar que
# los tokens crezcan O(N) con la longitud de la conversación.
MAX_USER_TURNS_TO_MODEL = 6


def _max_steps_for(alias: str) -> int:
    return MAX_STEPS_BY_ALIAS.get(alias, MAX_STEPS_DEFAULT)


def _is_legacy_gemini_history(history: list[dict[str, Any]] | None) -> bool:
    if not history:
        return False
    return any(isinstance(m, dict) and "parts" in m for m in history)


def _sanitize_history(history: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Limpia history en formato canónico o descarta si es legacy."""
    if _is_legacy_gemini_history(history):
        logger.info("history legacy (Gemini) detectado — descartando")
        return []
    return [m for m in (history or []) if isinstance(m, dict) and "role" in m]


def _is_real_user_message(msg: dict[str, Any]) -> bool:
    """True si el mensaje es texto del usuario (no un tool_result devuelto al modelo)."""
    if msg.get("role") != "user":
        return False
    content = msg.get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return not any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        )
    return False


def _truncate_for_model(messages: list[dict[str, Any]], max_user_turns: int) -> list[dict[str, Any]]:
    """Devuelve los últimos `max_user_turns` turnos (y todo lo encadenado).

    Corta en boundary de mensaje user real para no dejar tool_use huérfanos.
    Si hay menos turnos que el tope, devuelve la lista intacta.
    """
    user_indices = [i for i, m in enumerate(messages) if _is_real_user_message(m)]
    if len(user_indices) <= max_user_turns:
        return messages
    cut = user_indices[-max_user_turns]
    return messages[cut:]


def run_conversation(
    user_message: str,
    history: list[dict[str, Any]] | None = None,
    *,
    provider: LLMProvider | None = None,
    force_model: str | None = None,
    tools_list: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Corre una vuelta de conversación.

    Retorna:
        {
            "reply": str,
            "tool_calls": [{"name", "args", "ok"}],
            "history": [...]           # formato canónico para persistir
            "usage": {"promptTokenCount", "candidatesTokenCount", "totalTokenCount", "model", "model_alias"}
            "steps": int,
            "elapsed_s": float,
            "truncated": bool,
            "model_used": str          # alias (haiku | sonnet | gemini-flash)
        }
    """
    # Tools list del sistema (si no se pasa, cargar las definidas en tools.py)
    if tools_list is None:
        from api.agent.tools import TOOLS, _is_blocked
        tools_list = [t for t in TOOLS if not _is_blocked(t["endpoint"])]

    # Decidir modelo y provider
    if provider is None:
        model_alias = decide_model(user_message, force=force_model)
        provider = get_provider(model_alias)
    model_used = getattr(provider, "alias", getattr(provider, "model", "unknown"))

    # Dos listas:
    # - messages_full: el history que se devuelve al frontend para persistir
    #   y mostrar (el usuario sigue viendo la conversación entera).
    # - messages: lo que se envía al modelo en cada turn, truncado a los
    #   últimos N turnos de usuario para evitar que los tokens crezcan O(N).
    # Ambas crecen en paralelo dentro del loop.
    messages_full: list[dict[str, Any]] = _sanitize_history(history)
    messages_full.append(user_text_message(user_message))

    messages: list[dict[str, Any]] = _truncate_for_model(messages_full, MAX_USER_TURNS_TO_MODEL)
    if len(messages) < len(messages_full):
        logger.info(
            "history truncado para modelo: %d → %d mensajes (cap=%d turnos user)",
            len(messages_full), len(messages), MAX_USER_TURNS_TO_MODEL,
        )

    # System prompt dinámico (cacheable por Claude)
    try:
        market_ctx = build_market_context()
    except Exception:
        logger.exception("fallo market_context; sigo")
        market_ctx = ""
    try:
        data_inv = build_data_inventory()
    except Exception:
        logger.exception("fallo data_inventory; sigo")
        data_inv = ""
    system_prompt = build_system_prompt(market_ctx, data_inv)

    tool_calls_log: list[dict[str, Any]] = []
    # Usage acumulado a lo largo de los turns. Arrancamos con los counters en 0
    # y vamos sumando por turn. Esto permite medir el hit rate real del prompt
    # caching (cache_read_input_tokens / total input) en Manager.AsistenteLogs.
    acc_usage: dict[str, Any] = {
        "promptTokenCount": 0,
        "candidatesTokenCount": 0,
        "totalTokenCount": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "turns": 0,
    }

    def _acumular(u: dict[str, Any]) -> None:
        for k in (
            "promptTokenCount",
            "candidatesTokenCount",
            "totalTokenCount",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            acc_usage[k] += int(u.get(k, 0) or 0)
        acc_usage["turns"] += 1
        # model/model_alias los pisamos con el último — no cambian intra-conversación.
        if u.get("model"):
            acc_usage["model"] = u["model"]
        if u.get("model_alias"):
            acc_usage["model_alias"] = u["model_alias"]

    t_start = time.time()
    max_steps = _max_steps_for(model_used)

    for step in range(max_steps):
        try:
            resp: LLMResponse = provider.generate(
                messages=messages,
                system_prompt=system_prompt,
                tools=tools_list,
            )
        except LLMError:
            raise
        except Exception as e:
            logger.exception("error no controlado del provider")
            raise LLMError(f"error del provider: {e}") from e

        _acumular(resp.usage)

        # Apendear mensaje del asistente a las dos listas (full y la del modelo)
        messages.append(resp.assistant_message)
        messages_full.append(resp.assistant_message)

        # Si no pidió tools, cerramos
        if not resp.tool_calls:
            return {
                "reply": resp.text or "(respuesta vacía)",
                "tool_calls": tool_calls_log,
                "history": messages_full,
                "usage": acc_usage,
                "steps": step + 1,
                "elapsed_s": round(time.time() - t_start, 2),
                "truncated": False,
                "model_used": model_used,
            }

        # Ejecutar tool calls en paralelo cuando hay más de uno (I/O bound).
        # ThreadPoolExecutor.map preserva el orden por input, así el log y los
        # tool_result_message quedan alineados con el orden original.
        for tc in resp.tool_calls:
            logger.info("tool_call name=%s args=%s", tc.name, tc.args)

        if len(resp.tool_calls) == 1:
            tc = resp.tool_calls[0]
            results = [dispatch(tc.name, tc.args)]
        else:
            with ThreadPoolExecutor(max_workers=min(len(resp.tool_calls), 8)) as ex:
                results = list(ex.map(lambda tc: dispatch(tc.name, tc.args), resp.tool_calls))

        for tc, result in zip(resp.tool_calls, results, strict=True):
            tool_calls_log.append({
                "name": tc.name,
                "args": tc.args,
                "ok": bool(result.get("ok", False)),
            })
            tr_msg = tool_result_message(tc.id, result)
            messages.append(tr_msg)
            messages_full.append(tr_msg)

    # Llegamos al tope sin que el modelo cierre con texto. Devolvemos una
    # respuesta sintética que muestre qué alcanzamos a consultar (en vez del
    # mensaje crudo "reformulá", que tira el problema al usuario sin pista).
    logger.warning("max_steps=%d alcanzado (alias=%s)", max_steps, model_used)
    tools_invocadas = sorted({tc["name"] for tc in tool_calls_log})
    if tools_invocadas:
        reply = (
            f"Paré después de {max_steps} consultas encadenadas para controlar costos. "
            f"Alcancé a consultar: {', '.join(tools_invocadas)}. "
            f"Si querés profundizar, pedime algo más específico (ej. un instrumento "
            f"o tramo concreto)."
        )
    else:
        reply = (
            f"Paré después de {max_steps} pasos sin haber resuelto la consulta. "
            f"Reformulá siendo más específico."
        )
    return {
        "reply": reply,
        "tool_calls": tool_calls_log,
        "history": messages_full,
        "usage": acc_usage,
        "steps": max_steps,
        "elapsed_s": round(time.time() - t_start, 2),
        "truncated": True,
        "model_used": model_used,
    }
