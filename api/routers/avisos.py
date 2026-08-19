"""api/routers/avisos.py — LOS PENDIENTES QUE EL AGENTE LE DEJÓ A CADA PERSONA.

**Por qué vive FUERA de `/api/ia`** (2026-08-19). Regla del user:

    *«El AV AGENT es SOLO para admin, no para el resto. Aunque esto no quiere
    decir que no tenga el poder para mandar una alerta, notificación, etc. a otro
    user que no sea admin.»*

Y ahí había un bug real: la acción `avisar.responsable` deja el aviso en la base
con el email del destinatario, pero el endpoint para leerlo vivía bajo
`/api/ia`, que está gateado por el módulo **`ia` — que solo tienen admin e
invitado**. O sea que el agente le podía escribir a un trader y el trader **no
lo veía nunca**: el aviso quedaba guardado para nadie.

Por eso este router está aparte y **sin gate de módulo**. No es un agujero:

  · devuelve SOLO los avisos cuyo destinatario es el email del que pregunta —
    no hay parámetro para pedir los de otro, así que no hay nada que forzar;
  · un aviso dirigido dice QUÉ HACER y DÓNDE, no expone el estado interno del
    sistema (eso sigue siendo del agente, admin-only);
  · el portal invitado queda EXCLUIDO igual (REGLA #8): que hoy devolvería una
    lista vacía es una coincidencia de los datos, no una regla.

La separación es exactamente la que pidió el user: **el AGENTE es admin-only; lo
que el agente MANDA le llega a cualquiera.**
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth import get_user_email, is_guest_portal

router = APIRouter(prefix="/api/avisos", tags=["avisos"])


def _quien(request: Request, email: str) -> str:
    if is_guest_portal(request):
        raise HTTPException(403, "no disponible para el portal invitado")
    quien = (email or "").strip().lower()
    if not quien:
        raise HTTPException(401, "sin identidad")
    return quien


@router.get("")
def mis_avisos(request: Request, email: str = Depends(get_user_email)):
    """Lo que el agente te dejó a VOS. Filtra por tu propio email."""
    from api.services import av_agent_vista as vista
    return {"avisos": vista.avisos_de(_quien(request, email))}


class _Hecho(BaseModel):
    id: int = Field(..., ge=1)


@router.post("/hecho")
def marcar_hecho(request: Request, body: _Hecho = Body(...),
                 email: str = Depends(get_user_email)):
    """«Ya lo hice». **Solo sobre un aviso propio**: el service compara el
    destinatario contra quien pide, así que cerrar el de otro no es una decisión
    de permisos que alguien pueda olvidarse de chequear — es imposible."""
    from api.services import av_agent_vista as vista
    r = vista.resolver_aviso_propio(body.id, quien=_quien(request, email))
    if not r.get("ok"):
        raise HTTPException(404, r.get("error") or "no existe ese aviso")
    return r
