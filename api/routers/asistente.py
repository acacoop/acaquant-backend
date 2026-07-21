"""api/routers/asistente.py — ASISTENTE DE NEGOCIO (QuantAI P7, docs/QUANTAI.md).

Solo HTTP plumbing (la lógica vive en api/services/asistente.py). Gates
estructurales, en capas:
- Se monta en api/main.py SOLO si el transporte LLM está configurado
  (patrón MCP: sin credencial, el endpoint no existe).
- Dependencies del mount: bearer + require_module("asistente") — módulo
  admin-only por default, JAMÁS en INVITADO_MODULES (REGLA #8).
- Rate limit propio: cada pregunta cuesta tokens del proveedor.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.ratelimit import limiter
from api.services import asistente

router = APIRouter(prefix="/api/asistente", tags=["asistente"])


class ChatBody(BaseModel):
    mensaje: str = Field(min_length=1, max_length=4000)
    chat_id: str | None = Field(default=None, max_length=64)


@router.post("/chat")
@limiter.limit("10/minute;150/day")
def chat(request: Request, body: ChatBody, email: str = Depends(get_user_email)):
    """Un turno del chat. `chat_id` ausente → conversación nueva (el server
    la crea y la devuelve). Endpoint `def` (sync) a propósito: el LLM tarda
    segundos y FastAPI lo corre en threadpool sin bloquear el loop."""
    r = asistente.responder(mensaje=body.mensaje, email=email, chat_id=body.chat_id)
    if not r.get("ok") and r.get("motivo") == "chat_ajeno":
        raise HTTPException(status_code=403, detail="esa conversación no te pertenece")
    return r
