"""api/routers/manager/salud.py — el estado del sistema en UNA sola llamada.

Thin wrapper sobre `api/services/salud.py`. Reemplaza al recorrido por seis tabs
(CONTROLES / DIAGNÓSTICO / JOBS / BASE / LATENCIA / IA) con una pregunta: ¿está
todo bien? Ver el docstring del service para el porqué (incidente 2026-08-07).
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from api.services import salud as svc

router = APIRouter()


@router.get("/salud")
def get_salud(
    solo_problemas: bool = Query(False, description="omite los chequeos en verde"),
):
    """Veredicto único + todos los chequeos, peor primero.

    Un chequeo = {id, familia, titulo, estado, motivo, evidencia}. Se generan solos:
    los jobs salen de `deploy/crontab.txt` (un cron nuevo aparece sin tocar nada) y
    los datos, de los contratos de frescura del service.
    """
    r = svc.resumen()
    if solo_problemas:
        r["chequeos"] = [c for c in r["chequeos"] if c["estado"] != svc.OK]
    return r
