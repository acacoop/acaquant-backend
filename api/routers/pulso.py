"""`api/routers/pulso.py` — EL PULSO DEL CLIENTE. Doc: `docs/AGENT.md` §0.dg.

Una pantalla que lleva más de un minuto sin poder refrescar lo dice acá, una
vez por minuto como máximo (el freno vive en el navegador y se repite acá con
el rate limit). No es telemetría de uso: solo llega cuando algo falla. El
agente (`latencia` → `vista_ciega`) lo convierte en «la vista X estuvo ciega
N minutos, M personas la tenían abierta, causa: …».

**El mismo endpoint recibe el TILDE** (`tipo: "tilde"`, 2026-09-04): el
navegador avisa que el hilo principal quedó bloqueado N ms. Va por acá y no por
una ruta nueva porque es la misma pregunta con dos respuestas posibles —«¿por
qué la pantalla no anda?»— y separarlas en dos endpoints obligaba a mirar dos
lugares para contestarla. El agente lo lee como `pantalla_tildada`.

Sin gate de módulo (cualquier vista, cualquier rol) y sin invitado (REGLA #8:
escribe en la base y la bandeja del agente es de la mesa).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from agente import pulso as _pulso
from api.auth import get_user_email, require_no_invitado
from api.ratelimit import limiter

router = APIRouter(prefix="/api/pulso", tags=["pulso"],
                   dependencies=[Depends(require_no_invitado)])


class _Pulso(BaseModel):
    vista: str = Field(..., min_length=1, max_length=120)
    # Vacío en un `tilde`: ahí no hay un pedido que falle — lo que se clavó es
    # el navegador entero.
    endpoint: str = Field("", max_length=200)
    motivo: str = Field("", max_length=120)
    desde_at: str | None = Field(None, max_length=40)
    tipo: str = Field("ciega", max_length=20)
    ms: int | None = Field(None, ge=0, le=3_600_000)
    datos: dict = Field(default_factory=dict)


@router.post("")
@limiter.limit("12/minute;300/hour")
def pulso(request: Request, body: _Pulso,
          email: str = Depends(get_user_email)) -> dict:
    """Deja el pulso. Solo plumbing: la escritura vive en `agente/pulso.py`."""
    return _pulso.registrar(email=email, vista=body.vista, endpoint=body.endpoint,
                            motivo=body.motivo, desde_at=body.desde_at,
                            tipo=body.tipo, ms=body.ms, datos=body.datos)
