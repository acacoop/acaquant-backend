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

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import get_user_email, require_no_invitado

# ⚠️ **El rechazo del invitado va como DEPENDENCY del router, no como un `if`
# adentro de cada handler** (2026-08-19). Vivía adentro y funcionaba igual, pero:
#
#   · un endpoint nuevo acá tendría que ACORDARSE de llamarlo, y
#   · **no era visible desde afuera**: ni el test de superficie ni el propio
#     agente pueden ver un `if` en el cuerpo de una función, así que este router
#     figuraba como escritura sin ningún gate. Un permiso que existe pero no se
#     puede auditar es, para cualquier herramienta, un permiso que no existe.
router = APIRouter(prefix="/api/avisos", tags=["avisos"],
                   dependencies=[Depends(require_no_invitado)])


def _quien(email: str) -> str:
    """El invitado ya quedó afuera por la dependency del router; acá solo se
    resuelve la identidad."""
    quien = (email or "").strip().lower()
    if not quien:
        raise HTTPException(401, "sin identidad")
    return quien


@router.get("")
def mis_avisos(email: str = Depends(get_user_email)):
    """Lo que el agente te dejó a VOS. Filtra por tu propio email."""
    from api.services import av_agent_vista as vista
    return {"avisos": vista.avisos_de(_quien(email))}


class _ItemHecho(BaseModel):
    id: int = Field(..., ge=1)
    hecho: bool = True


@router.post("/item")
def marcar_item(body: _ItemHecho = Body(...),
                email: str = Depends(get_user_email)):
    """Tilda UNA fila de un aviso que trae tabla (los saldos del día, por
    ejemplo). Cada tilde queda con quién y cuándo.

    **Solo sobre un aviso propio**, y eso vive en el WHERE del UPDATE: tildar la
    fila de otro no es una decisión de permisos que alguien pueda olvidarse de
    chequear — es imposible.

    Cuando no queda ninguna pendiente el aviso se cierra solo: pedir además que
    aprieten «listo» sería un paso que no agrega nada."""
    from api.services import av_agent_mensajes as msg
    r = msg.marcar_item(body.id, quien=_quien(email), hecho=body.hecho)
    if not r.get("ok"):
        raise HTTPException(404, r.get("error") or "no existe esa fila")
    return r


class _Hecho(BaseModel):
    id: int = Field(..., ge=1)


@router.post("/hecho")
def marcar_hecho(body: _Hecho = Body(...),
                 email: str = Depends(get_user_email)):
    """«Ya lo hice». **Solo sobre un aviso propio**: el service compara el
    destinatario contra quien pide, así que cerrar el de otro no es una decisión
    de permisos que alguien pueda olvidarse de chequear — es imposible."""
    from api.services import av_agent_vista as vista
    r = vista.resolver_aviso_propio(body.id, quien=_quien(email))
    if not r.get("ok"):
        raise HTTPException(404, r.get("error") or "no existe ese aviso")
    return r
