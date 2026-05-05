"""Router /api/valuaciones — performance e historia por cuenta.

Endpoints principales (AUM-based, vista actual):
- GET /{id_cuenta}/serie    — daily portfolio total (Valuaciones.AuM).
- GET /{id_cuenta}/mensual  — cierre mensual + flujos externos.

Endpoint legacy (cost-basis ledger, para drill-down per-ticker en Phase 2):
- GET /{id_cuenta}/posiciones — weighted-avg cost + PnL realizado/no real.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from api.services import valuaciones as svc

logger = logging.getLogger("api.valuaciones")

router = APIRouter(prefix="/api/valuaciones", tags=["Valuaciones"])


def _validate_id_cuenta(id_cuenta: str) -> None:
    if not id_cuenta or not id_cuenta.isdigit():
        raise HTTPException(400, f"id_cuenta inválida: {id_cuenta!r}")


@router.get("/{id_cuenta}/serie")
def get_serie(
    id_cuenta: str,
    desde: str | None = Query(None, description="YYYY-MM-DD inclusive"),
    hasta: str | None = Query(None, description="YYYY-MM-DD inclusive"),
):
    """Serie diaria del valor total del portfolio (Valuaciones.AuM)."""
    _validate_id_cuenta(id_cuenta)
    try:
        return svc.serie_valor_cuenta(id_cuenta=id_cuenta, desde=desde, hasta=hasta)
    except Exception as e:
        logger.exception(
            "valuaciones serie failed: id_cuenta=%s desde=%s hasta=%s",
            id_cuenta, desde, hasta,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{id_cuenta}/mensual")
def get_mensual(id_cuenta: str):
    """Tabla mensual: cierre del mes (último fecha_snapshot) +
    flujos externos del mes (depósitos − extracciones)."""
    _validate_id_cuenta(id_cuenta)
    try:
        return svc.valuacion_mensual(id_cuenta=id_cuenta)
    except Exception as e:
        logger.exception("valuaciones mensual failed: id_cuenta=%s", id_cuenta)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{id_cuenta}/posiciones-actuales")
def get_posiciones_actuales(id_cuenta: str):
    """Posiciones al último fecha_snapshot disponible — pure AuM read,
    sin cost basis ni PnL. Para el panel derecho de /valuaciones."""
    _validate_id_cuenta(id_cuenta)
    try:
        return svc.posiciones_actuales(id_cuenta=id_cuenta)
    except Exception as e:
        logger.exception(
            "valuaciones posiciones-actuales failed: id_cuenta=%s", id_cuenta,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{id_cuenta}/posiciones")
def get_posiciones(
    id_cuenta: str,
    hasta: str | None = Query(
        None,
        description="YYYY-MM-DD inclusive. None = todos los boletos hasta hoy.",
    ),
):
    """Posiciones actuales de la cuenta con cost basis weighted-average,
    PnL realizado + no realizado, marcadas con completeness por ticker.

    Legacy / Phase 2 — la vista principal usa /serie y /mensual.
    """
    _validate_id_cuenta(id_cuenta)
    try:
        return svc.posiciones_cuenta(id_cuenta=id_cuenta, hasta=hasta)
    except Exception as e:
        logger.exception(
            "valuaciones posiciones failed: id_cuenta=%s hasta=%s",
            id_cuenta, hasta,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e
