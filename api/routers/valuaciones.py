"""Router /api/valuaciones — vista de cost-basis PnL por cuenta.

MVP scope: solo posiciones actuales con cost basis weighted-average.
Phase 2 sumará serie temporal, TWR, decomposición por clase, comparación
contra benchmark.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from api.services import valuaciones as svc

logger = logging.getLogger("api.valuaciones")

router = APIRouter(prefix="/api/valuaciones", tags=["Valuaciones"])


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
    """
    if not id_cuenta or not id_cuenta.isdigit():
        raise HTTPException(400, f"id_cuenta inválida: {id_cuenta!r}")
    try:
        return svc.posiciones_cuenta(id_cuenta=id_cuenta, hasta=hasta)
    except Exception as e:
        logger.exception(
            "valuaciones posiciones failed: id_cuenta=%s hasta=%s",
            id_cuenta, hasta,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e
