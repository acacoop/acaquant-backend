"""La traza de cada llamada a un modelo: un callback de LangChain que escribe
una fila en `ia.llamadas` al terminar (bien o mal). Doc: docs/AvAgentAI.md."""
from __future__ import annotations

import logging
import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult

logger = logging.getLogger(__name__)

MAX_ERROR_CHARS = 700
MAX_DETALLE_CHARS = 600
MAX_RESPUESTA_CHARS = 1500


class Traza(BaseCallbackHandler):
    """Una instancia por llamada lógica (un agente, una vuelta o varias). Guarda
    los ids de las filas que escribió en `ids`."""

    def __init__(self, tarea: str, modelo: str, *, usuario: str | None = None,
                 sesion: str | None = None, detalle: str | None = None,
                 guardar_texto: bool = True) -> None:
        self.tarea, self.modelo, self.usuario, self.sesion = tarea, modelo, usuario, sesion
        # Extracto del pedido para el libro. Si el que llama no lo da, se toma
        # el último mensaje del usuario. Con `guardar_texto=False` (dato
        # personal) no se guarda ni el pedido ni la respuesta: solo números.
        self.detalle = detalle
        self.guardar_texto = guardar_texto
        self.ids: list[int] = []
        self._inicio: dict[UUID, tuple[float, str | None]] = {}

    # LangChain llama a esto con la lista de mensajes de cada invocación.
    def on_chat_model_start(self, serialized: dict, messages: list[list[BaseMessage]], *,
                            run_id: UUID, **_kw: Any) -> None:
        detalle = self.detalle
        if detalle is None:
            for m in reversed(messages[0] if messages else []):
                if m.type == "human":
                    detalle = _texto(m.content)
                    break
        self._inicio[run_id] = (time.perf_counter(), detalle)

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **_kw: Any) -> None:
        t0, detalle = self._inicio.pop(run_id, (None, None))
        gen = response.generations[0][0] if response.generations and response.generations[0] else None
        msg = getattr(gen, "message", None)
        uso = (getattr(msg, "usage_metadata", None) or {}) if msg is not None else {}
        meta = (getattr(msg, "response_metadata", None) or {}) if msg is not None else {}
        tokens_in = uso.get("input_tokens")
        tokens_out = uso.get("output_tokens")
        cache_hit = (uso.get("input_token_details") or {}).get("cache_read")
        if cache_hit is None:
            cache_hit = (meta.get("token_usage") or {}).get("prompt_cache_hit_tokens")
        cache_miss = (tokens_in - cache_hit) if tokens_in is not None and cache_hit is not None else None
        texto = _texto(msg.content) if msg is not None else ""
        pidio = bool(getattr(msg, "tool_calls", None) or getattr(msg, "invalid_tool_calls", None))
        self._escribir(ok=bool(texto) or pidio, error=None if (texto or pidio) else "respuesta vacía",
                       tokens_in=tokens_in, tokens_out=tokens_out, cache_hit=cache_hit,
                       cache_miss=cache_miss, latencia_ms=_ms(t0), detalle=detalle,
                       respuesta=texto or None, modelo=meta.get("model_name") or self.modelo)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **_kw: Any) -> None:
        t0, detalle = self._inicio.pop(run_id, (None, None))
        self._escribir(ok=False, error=f"{type(error).__name__}: {error}", tokens_in=None,
                       tokens_out=None, cache_hit=None, cache_miss=None, latencia_ms=_ms(t0),
                       detalle=detalle, respuesta=None, modelo=self.modelo)

    def _escribir(self, *, ok: bool, error: str | None, tokens_in, tokens_out, cache_hit,
                  cache_miss, latencia_ms, detalle, respuesta, modelo: str) -> None:
        try:
            from core.postgres import get_pool
            with get_pool().connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO ia.llamadas (tarea, modelo, usuario, tokens_in, tokens_out,"
                    " latencia_ms, ok, error, detalle, respuesta, cache_hit_tokens,"
                    " cache_miss_tokens, sesion)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (self.tarea, modelo, self.usuario, tokens_in, tokens_out, latencia_ms, ok,
                     error[:MAX_ERROR_CHARS] if error else None,
                     detalle[:MAX_DETALLE_CHARS] if detalle and self.guardar_texto else None,
                     respuesta[:MAX_RESPUESTA_CHARS] if respuesta and self.guardar_texto else None,
                     cache_hit, cache_miss, self.sesion))
                self.ids.append(cur.fetchone()[0])
        except Exception as e:
            logger.warning("traza: no pude registrar la llamada de %s (%s)", self.tarea, e)


def _ms(t0: float | None) -> int | None:
    return int((time.perf_counter() - t0) * 1000) if t0 is not None else None


def _texto(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(c.get("text", "")) if isinstance(c, dict) else str(c) for c in content)
    return str(content or "")
