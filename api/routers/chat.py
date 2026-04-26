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
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from starlette.requests import Request

from api.agent.provider import (
    LLMBadResponseError,
    LLMError,
    LLMRateLimitError,
    LLMTransportError,
)
from api.agent.runner import run_conversation
from api.auth import get_user_email
from api.ratelimit import limiter
from config import ANTHROPIC_API_KEY, GEMINI_API_KEY, LLM_PROVIDER
from core.mongo import get_mongo_client

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    history: list[dict[str, Any]] | None = None
    # ID de conversación opcional. Si no viene, el backend genera uno nuevo y
    # lo devuelve en la response — el frontend debe guardarlo y mandarlo en
    # los siguientes turns para agrupar la conversación entera en logs.
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    tool_calls: list[dict[str, Any]]
    history: list[dict[str, Any]]
    usage: dict[str, Any]
    steps: int
    elapsed_s: float
    truncated: bool = False
    model_used: str = ""
    conversation_id: str = ""


def _log_interaccion(
    req: ChatRequest,
    resp: dict[str, Any] | None,
    user_email: str,
    conversation_id: str,
    error: dict[str, Any] | None = None,
) -> None:
    """Persiste cada turno en Manager.AsistenteLogs para auditoría.

    `conversation_id` es siempre el resuelto (nunca None) para poder agrupar
    todos los turns de una misma conversación en el dashboard.
    """
    try:
        doc: dict[str, Any] = {
            "ts": datetime.now(UTC),
            "user": user_email or "anon",
            "conversation_id": conversation_id,
            "message": req.message,
            "estado": "error" if error else ("truncated" if resp and resp.get("truncated") else "ok"),
        }
        if resp:
            doc.update({
                "reply": resp.get("reply", ""),
                "tool_calls": resp.get("tool_calls", []),
                "usage": resp.get("usage", {}),
                "steps": resp.get("steps", 0),
                "elapsed_s": resp.get("elapsed_s", 0),
                "truncated": resp.get("truncated", False),
                "history_len": len(resp.get("history", [])),
                "model_used": resp.get("model_used", ""),
            })
        if error:
            doc["error"] = error
        get_mongo_client()["Manager"]["AsistenteLogs"].insert_one(doc)
    except Exception:
        logger.exception("no se pudo loggear la interacción")


@router.post("", response_model=ChatResponse)
@limiter.limit("30/minute;500/day")
def chat(
    request: Request,  # requerido por slowapi para aplicar key_func
    req: ChatRequest,
) -> ChatResponse:
    # Resolución inline del email: si hay CF_ACCESS_TEAM+AUD, valida el JWT;
    # si no, cae al header. Evitamos Depends() acá porque no se mezcla bien
    # con Request + BaseModel body en la misma firma.
    user_email = get_user_email(
        cf_jwt=request.headers.get("cf-access-jwt-assertion"),
        cf_email=request.headers.get("cf-access-authenticated-user-email"),
    )

    # Resolver conversation_id: si el frontend mandó uno, lo respetamos; si
    # no, generamos uno nuevo (primer turn de una conversación). El ID se
    # devuelve en la response y se persiste en cada doc de AsistenteLogs.
    conversation_id = (req.conversation_id or "").strip() or uuid.uuid4().hex

    # Validar que haya key del provider activo
    if LLM_PROVIDER == "claude" and not ANTHROPIC_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="ANTHROPIC_API_KEY no configurada (LLM_PROVIDER=claude).",
        )
    if LLM_PROVIDER == "gemini" and not GEMINI_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="GEMINI_API_KEY no configurada (LLM_PROVIDER=gemini).",
        )

    try:
        result = run_conversation(
            user_message=req.message,
            history=req.history,
        )
    except LLMRateLimitError as e:
        logger.warning("rate limit llm: %s", e)
        _log_interaccion(req, None, user_email, conversation_id, error={"code": "rate_limit", "message": str(e)[:300]})
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
        logger.warning("transport error llm: %s", e)
        _log_interaccion(req, None, user_email, conversation_id, error={"code": "transport", "message": str(e)[:300]})
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
        logger.warning("bad response llm: %s", e)
        _log_interaccion(req, None, user_email, conversation_id, error={"code": "bad_response", "message": str(e)[:300]})
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
        _log_interaccion(req, None, user_email, conversation_id, error={"code": "llm_error", "message": str(e)[:300]})
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
        _log_interaccion(req, None, user_email, conversation_id, error={"code": "internal", "message": str(e)[:300]})
        raise HTTPException(
            status_code=500,
            detail={
                "code": "internal",
                "message": "Error interno del servidor.",
                "retryable": False,
            },
        ) from e

    _log_interaccion(req, result, user_email, conversation_id)
    result["conversation_id"] = conversation_id
    # ChatResponse no conoce los nuevos campos del flow estructurado — los
    # filtramos para no romper la validación.
    response_fields = set(ChatResponse.model_fields.keys())
    return ChatResponse(**{k: v for k, v in result.items() if k in response_fields})


# ═══════════════════════════════════════════════════════════════════════════
# Flow estructurado: Recomendar cartera
# ═══════════════════════════════════════════════════════════════════════════
#
# A diferencia de POST /api/chat (chat libre), acá el usuario llena un
# formulario tipado en el frontend, el backend construye el user message
# determinístico y fuerza al modelo a responder vía la tool `responder_cartera`.
# Garantiza output con shape fijo (tesis + cartera + que_invalida + alertas).
# Ver api/agent/structured/cartera.py para el schema y la lógica.

from api.agent.structured import CarteraRequest, run_cartera_flow  # noqa: E402


class CarteraResponse(BaseModel):
    """Response del flow estructurado de cartera. `data` es None si el
    modelo no produjo output estructurado válido (ej. truncado por max_steps
    o falló alguna parte crítica)."""

    data: dict[str, Any] | None  # los args de responder_cartera tal cual
    tool_calls: list[dict[str, Any]]
    usage: dict[str, Any]
    steps: int
    elapsed_s: float
    truncated: bool = False
    model_used: str = ""
    pesos_ok: bool = True   # si la suma de peso_pct dio 100 (±0.5)
    pesos_suma: float = 0.0
    error: str | None = None  # si data es None, qué falló


def _log_structured_cartera(
    req: CarteraRequest,
    resp: dict[str, Any] | None,
    user_email: str,
    conversation_id: str,
    error: dict[str, Any] | None = None,
) -> None:
    """Log dedicado para el flow de cartera. Usa `tipo: structured_cartera`
    en Manager.AsistenteLogs para poder filtrar en el dashboard separado del
    chat libre.

    `conversation_id` y `message` se setean siempre para que el dashboard
    del manager (que agrupa por conversation_id y muestra `message` como
    preview) renderice cada request como una conversación propia."""
    try:
        # Texto descriptivo derivado del request — se usa como preview en el
        # dashboard. Los flows estructurados son one-shot, no hay free text.
        msg_preview = (
            f"[Cartera] {req.perfil} / {req.exposicion} / {req.plazo} / {req.benchmark}"
        )
        doc: dict[str, Any] = {
            "ts": datetime.now(UTC),
            "user": user_email or "anon",
            "conversation_id": conversation_id,
            "message": msg_preview,
            "tipo": "structured_cartera",
            "metadata": req.model_dump(),
            "estado": "error" if error else ("truncated" if resp and resp.get("truncated") else "ok"),
        }
        if resp:
            doc.update({
                "structured_output": resp.get("structured_output"),
                "tool_calls": resp.get("tool_calls", []),
                "usage": resp.get("usage", {}),
                "steps": resp.get("steps", 0),
                "elapsed_s": resp.get("elapsed_s", 0),
                "truncated": resp.get("truncated", False),
                "model_used": resp.get("model_used", ""),
                "pesos_ok": resp.get("pesos_ok", True),
                "pesos_suma": resp.get("pesos_suma", 0),
            })
        if error:
            doc["error"] = error
        get_mongo_client()["Manager"]["AsistenteLogs"].insert_one(doc)
    except Exception:
        logger.exception("no se pudo loggear cartera structured")


@router.post("/structured/cartera", response_model=CarteraResponse)
@limiter.limit("10/minute;200/day")
def chat_structured_cartera(
    request: Request,  # requerido por slowapi
    req: CarteraRequest,
) -> CarteraResponse:
    """Construye una cartera recomendada según los params del formulario.

    El frontend valida los enums vía OpenAPI; acá Pydantic re-valida.
    Output: el args de la tool `responder_cartera` que el modelo emite,
    más metadata de ejecución (tools, usage, latencia).
    """
    user_email = get_user_email(
        cf_jwt=request.headers.get("cf-access-jwt-assertion"),
        cf_email=request.headers.get("cf-access-authenticated-user-email"),
    )

    # Cada request del flow estructurado es one-shot — generamos un
    # conversation_id propio para que el dashboard del manager agrupe
    # cada intento como una conversación propia (en vez de caer en el
    # bucket "(legacy)" por ausencia del campo).
    conversation_id = uuid.uuid4().hex

    if LLM_PROVIDER == "claude" and not ANTHROPIC_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="ANTHROPIC_API_KEY no configurada.",
        )

    try:
        result = run_cartera_flow(req)
    except LLMRateLimitError as e:
        logger.warning("rate limit cartera: %s", e)
        _log_structured_cartera(req, None, user_email, conversation_id, error={"code": "rate_limit", "message": str(e)[:300]})
        raise HTTPException(
            status_code=429,
            detail={"code": "rate_limit", "message": str(e), "retryable": True, "retry_after_s": 60},
        ) from e
    except LLMTransportError as e:
        logger.warning("transport cartera: %s", e)
        _log_structured_cartera(req, None, user_email, conversation_id, error={"code": "transport", "message": str(e)[:300]})
        raise HTTPException(
            status_code=503,
            detail={"code": "transport", "message": "No pude conectar con el modelo.", "retryable": True},
        ) from e
    except LLMError as e:
        logger.exception("LLMError cartera")
        _log_structured_cartera(req, None, user_email, conversation_id, error={"code": "llm_error", "message": str(e)[:300]})
        raise HTTPException(
            status_code=502,
            detail={"code": "llm_error", "message": "Error del modelo.", "retryable": True},
        ) from e
    except Exception as e:
        logger.exception("error inesperado cartera")
        _log_structured_cartera(req, None, user_email, conversation_id, error={"code": "internal", "message": str(e)[:300]})
        raise HTTPException(
            status_code=500,
            detail={"code": "internal", "message": "Error interno.", "retryable": False},
        ) from e

    _log_structured_cartera(req, result, user_email, conversation_id)

    structured = result.get("structured_output") or {}
    args = structured.get("args") if structured.get("name") == "responder_cartera" else None
    error_msg = None
    if args is None:
        # El modelo no llamó la tool — caso raro porque la forzamos en el
        # último step, pero podría pasar si truncated o si el modelo solo
        # llamó tools de data y nunca cerró.
        error_msg = (
            "El modelo no produjo cartera estructurada. "
            "Probá con otros parámetros o reintentá."
        )

    return CarteraResponse(
        data=args,
        tool_calls=result.get("tool_calls", []),
        usage=result.get("usage", {}),
        steps=result.get("steps", 0),
        elapsed_s=result.get("elapsed_s", 0),
        truncated=result.get("truncated", False),
        model_used=result.get("model_used", ""),
        pesos_ok=result.get("pesos_ok", True),
        pesos_suma=result.get("pesos_suma", 0),
        error=error_msg,
    )
