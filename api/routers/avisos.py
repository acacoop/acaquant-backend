"""`api/routers/avisos.py` — LO QUE EL AGENTE TE MANDÓ A VOS. Doc: `docs/AGENT.md` §0.de.

**Sin gate de módulo, a propósito.** El agente es admin-only, pero lo que el
agente MANDA le tiene que llegar a cualquiera: `jobs/saldos_a_operadores`
le deja a cada operador sus cuentas con saldo a las 16:45, y un operador no
tiene el módulo `ia`. Devuelve SOLO los del email que pregunta — no hay
parámetro para pedir los de otro — y solo los de HOY: una comunicación es del
día (`agente/mensajes.py`), no se acumula.

Este archivo estuvo documentado en `api/main.py` desde el 2026-08-19 y no
existió hasta el 2026-09-02: la pantalla «PARA VOS» pedía `/api/avisos`, el
proxy de Next lo reenviaba, y el backend contestaba 404 en silencio. La
bandeja se escribía todos los días para nadie.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from agente import mensajes
from api.auth import get_user_email, is_guest_portal, require_no_invitado

# Sin módulo, pero NUNCA para el invitado (REGLA #8): la bandeja es negocio
# de la mesa, y `test_rbac_superficie` exige que toda escritura sin módulo
# lleve `require_no_invitado`.
router = APIRouter(prefix="/api/avisos", tags=["avisos"],
                   dependencies=[Depends(require_no_invitado)])


@router.get("")
def bandeja(request: Request, email: str = Depends(get_user_email)) -> dict:
    """Los avisos de HOY para el que pregunta. El invitado (www) no tiene
    bandeja: es otro sector, y esto es negocio de la mesa (REGLA #8)."""
    if is_guest_portal(request):
        return {"avisos": []}
    return {"avisos": mensajes.de(email)}


class _Visto(BaseModel):
    id: int = Field(..., ge=1)


@router.post("/visto")
def visto(body: _Visto, email: str = Depends(get_user_email)) -> dict:
    """«Ya lo vi». Solo el destinatario puede marcarlo: el email va en el WHERE."""
    return mensajes.marcar_visto(body.id, quien=email)
