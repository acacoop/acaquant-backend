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

MAX_RANGO_DIAS = 60  # el mismo tope que impone Interbanking por consulta


def _rango(desde: date | None, hasta: date | None) -> tuple[date, date]:
    """Completa y valida el rango de las dos sub-tabs.

    El default lo decide el SERVICE (`bancos.rango_default`): es el día HÁBIL
    anterior y hoy, no "ayer y hoy" de calendario. Un lunes —o un martes
    post-feriado, como el 2026-08-18— restar un día apunta a una fecha sin
    actividad bancaria y la pantalla sale vacía, que el back office lee como
    "no hubo movimientos" en vez de "el rango está mal elegido".

    Está escrito UNA vez y lo usan los dos endpoints: si divergieran, el
    CONSOLIDADO y el DETALLE mostrarían períodos distintos.
    """
    d0, hasta = _svc.rango_default(hasta)
    desde = desde or d0
    if desde > hasta:
        raise HTTPException(400, "La fecha desde no puede ser posterior a hasta.")
    if (hasta - desde).days > MAX_RANGO_DIAS:
        raise HTTPException(400, f"El rango máximo es de {MAX_RANGO_DIAS} días.")
    return desde, hasta


@router.get("/vista")
def vista(
    cuenta_id: int | None = Query(None, description="cuenta de bancos.cuentas"),
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
    email: str = Depends(get_user_email),
) -> dict:
    """Todo lo que muestra la tab, en UN request: cuentas, extracto por día,
    movimientos, resumen de conciliación y cuándo fue la última sincronización."""
    desde, hasta = _rango(desde, hasta)
    return _svc.vista(email, cuenta_id, desde, hasta)


@router.get("/consolidado")
def consolidado(
    desde: date | None = Query(None),
    hasta: date | None = Query(None),
    email: str = Depends(get_user_email),
) -> dict:
    """CONSOLIDADO BANCOS: una fila por cuenta, agrupada por banco, con el saldo
    al inicio y al cierre del rango. Totales por banco y globales, por moneda."""
    desde, hasta = _rango(desde, hasta)
    return _svc.consolidado(email, desde, hasta)


@router.get("/cuentas")
def cuentas() -> list[dict]:
    """Solo el selector de cuentas (sin CBU ni número completo)."""
    return _svc.listar_cuentas()
