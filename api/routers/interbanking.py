"""api/routers/interbanking.py — tab INTERBANKING del BACK OFFICE.

Plumbing HTTP puro: la lógica vive en `api/services/bancos.py`.

SOLO LECTURA — no hay un solo endpoint de escritura, ni acá ni en el cliente
(`core/interbanking.py` implementa únicamente GET). Los datos los trae
`jobs/interbanking_sync`; esta vista lee de Postgres y nunca sale a Interbanking.

Gate: se monta en `api/main.py` con `_BACK_OFFICE`, o sea `require_module(
"back-office")`. **JAMÁS al portal invitado** (REGLA #8) — son los saldos
bancarios de la casa; hay un test que lo congela.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import get_user_email
from api.services import bancos as _svc

router = APIRouter(prefix="/api/back-office/interbanking", tags=["Interbanking"])


def _fecha(fecha: date | None) -> date:
    """La fecha que mira la vista. **Una sola, no un rango.**

    El back office la cambió el 2026-08-18: «la fecha es una sola, es siempre el
    mismo día». Antes eran `desde`/`hasta` y el consolidado terminaba mostrando
    la apertura de un día contra el cierre de otro — una variación de nada.

    Sin `fecha`, el default lo decide el SERVICE (`bancos.fecha_default`), no el
    navegador: así la pantalla no depende del reloj ni de la zona horaria del
    cliente.
    """
    if fecha and fecha > _svc.fecha_default():
        raise HTTPException(400, "No se puede pedir una fecha futura.")
    return fecha or _svc.fecha_default()


@router.get("/vista")
def vista(
    cuenta_id: int | None = Query(None, description="cuenta de bancos.cuentas"),
    fecha: date | None = Query(None, description="día a mostrar (default: hoy)"),
    email: str = Depends(get_user_email),
) -> dict:
    """Todo lo que muestra la tab, en UN request: cuentas, extracto del día,
    movimientos, resumen de conciliación y cuándo fue la última sincronización."""
    return _svc.vista(email, cuenta_id, _fecha(fecha))


@router.get("/consolidado")
def consolidado(
    fecha: date | None = Query(None, description="día a mostrar (default: hoy)"),
    email: str = Depends(get_user_email),
) -> dict:
    """CONSOLIDADO BANCOS: una fila por cuenta, agrupada por banco, con la
    apertura y el cierre de ESE día."""
    return _svc.consolidado(email, _fecha(fecha))


@router.get("/cuentas")
def cuentas() -> list[dict]:
    """Solo el selector de cuentas (sin CBU ni número completo)."""
    return _svc.listar_cuentas()
