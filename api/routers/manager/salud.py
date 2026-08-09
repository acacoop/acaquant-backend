"""api/routers/manager/salud.py — el estado del sistema en UNA sola llamada.

Thin wrapper sobre `api/services/salud.py`. Reemplaza el recorrido por seis tabs
(CONTROLES / DIAGNÓSTICO / JOBS / BASE / LATENCIA / IA) con una pregunta: ¿está
todo bien? Ver el docstring del service para el porqué (incidente 2026-08-07).
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services import salud as svc

router = APIRouter()


class _Alerta(BaseModel):
    chequeo_id: str = Field(..., min_length=1, max_length=200)
    alertar: bool
    nota: str = Field("", max_length=500)


class _Vistos(BaseModel):
    # Vacío = "entendido, no me lo muestres más" sobre todo lo pendiente.
    ids: list[int] = Field(default_factory=list)


@router.get("/salud")
def get_salud(
    solo_problemas: bool = Query(False, description="omite los chequeos en verde"),
    email: str = Depends(get_user_email),
):
    """Veredicto único + chequeos (peor primero) + lo pendiente de ver.

    Un chequeo = {id, familia, titulo, estado, motivo, evidencia, alertar}. Se
    generan solos: los jobs salen de `deploy/crontab.txt` (un cron nuevo aparece sin
    tocar nada) y los datos, de los contratos de frescura del service.

    Esta llamada además SINCRONIZA: registra las transiciones que hubo desde la
    última vez. Es idempotente — si nada cambió de estado, no escribe nada.
    """
    r = svc.panel(email=email)
    if solo_problemas:
        r["chequeos"] = [c for c in r["chequeos"] if c["estado"] != svc.OK]
    return r


@router.get("/salud/historial")
def get_historial(
    chequeo_id: str = Query("", description="vacío = todos"),
    limite: int = Query(50, ge=1, le=500),
):
    """El LOG de un chequeo: cuándo se rompió y cuándo volvió, con la evidencia
    congelada de cada momento (después el motivo ya no existe en ningún lado)."""
    return {"eventos": svc.historial(chequeo_id=chequeo_id, limite=limite)}


@router.put("/salud/alerta")
def put_alerta(req: _Alerta = Body(...), actor: str = Depends(get_user_email)):
    """Silenciar o reactivar un chequeo. Silenciar NO lo saca de la pantalla: sigue
    rojo en la lista, solo deja de abrir el modal."""
    try:
        return svc.set_alerta(req.chequeo_id, req.alertar, actor, req.nota)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/salud/vistos")
def post_vistos(req: _Vistos = Body(default=_Vistos()),
                email: str = Depends(get_user_email)):
    """Marca eventos como vistos por este admin (el 'entendido' del modal)."""
    try:
        return svc.marcar_vistos(email, req.ids)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
