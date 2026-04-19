"""POST /api/chat — endpoint del asistente de mesa.

Body:
    {
        "message": "pregunta del usuario",
        "history": [...]   // opcional, mensajes previos en formato Gemini
    }

Respuesta:
    {
        "reply": "texto final",
        "tool_calls": [{"name": ..., "args": ..., "ok": bool}],
        "history": [...],
        "usage": {"promptTokenCount": int, "candidatesTokenCount": int, "totalTokenCount": int},
        "steps": int,
        "elapsed_s": float
    }
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.agent.provider import (
    GeminiProvider,
    LLMBadResponseError,
    LLMError,
    LLMRateLimitError,
    LLMTransportError,
)
from api.agent.runner import run_conversation
from config import GEMINI_API_KEY
from core.mongo import get_mongo_client

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    history: list[dict[str, Any]] | None = None


class ChatResponse(BaseModel):
    reply: str
    tool_calls: list[dict[str, Any]]
    history: list[dict[str, Any]]
    usage: dict[str, Any]
    steps: int
    elapsed_s: float
    truncated: bool = False


def _log_interaccion(req: ChatRequest, resp: dict[str, Any]) -> None:
    """Persiste cada turno en Manager.AsistenteLogs para auditoría."""
    try:
        doc = {
            "ts": datetime.now(timezone.utc),
            "message": req.message,
            "reply": resp.get("reply", ""),
            "tool_calls": resp.get("tool_calls", []),
            "usage": resp.get("usage", {}),
            "steps": resp.get("steps", 0),
            "elapsed_s": resp.get("elapsed_s", 0),
            "truncated": resp.get("truncated", False),
            "history_len": len(resp.get("history", [])),
        }
        get_mongo_client()["Manager"]["AsistenteLogs"].insert_one(doc)
    except Exception:
        logger.exception("no se pudo loggear la interacción")


@router.post("", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    if not GEMINI_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="GEMINI_API_KEY no configurada en .env",
        )

    try:
        provider = GeminiProvider(api_key=GEMINI_API_KEY)
        result = run_conversation(
            provider=provider,
            user_message=req.message,
            history=req.history,
        )
    except LLMRateLimitError as e:
        logger.warning("rate limit Gemini: %s", e)
        raise HTTPException(
            status_code=429,
            detail={
                "code": "rate_limit",
                "message": str(e),
                "retryable": True,
                "retry_after_s": 60,
            },
        ) from e
    except LLMTransportError as e:
        logger.warning("transport error Gemini: %s", e)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "transport",
                "message": "No pude conectar con el modelo. Reintentá en unos segundos.",
                "retryable": True,
                "retry_after_s": 10,
            },
        ) from e
    except LLMBadResponseError as e:
        logger.warning("bad response Gemini: %s", e)
        raise HTTPException(
            status_code=502,
            detail={
                "code": "bad_response",
                "message": "El modelo devolvió una respuesta inválida. Reintentá.",
                "detail": str(e)[:200],
                "retryable": True,
            },
        ) from e
    except LLMError as e:
        logger.exception("LLMError genérico")
        raise HTTPException(
            status_code=502,
            detail={
                "code": "llm_error",
                "message": "Error del modelo.",
                "detail": str(e)[:200],
                "retryable": True,
            },
        ) from e
    except Exception as e:
        logger.exception("error inesperado en /api/chat")
        raise HTTPException(
            status_code=500,
            detail={
                "code": "internal",
                "message": "Error interno del servidor.",
                "retryable": False,
            },
        ) from e

    _log_interaccion(req, result)
    return ChatResponse(**result)
