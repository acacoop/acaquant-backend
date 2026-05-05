"""Manager · Aunesa — endpoint exploratorio en vivo.

Thin wrapper sobre `api/services/aunesa_negocio.py`. Para discovery /
debugging desde el panel /manager. La vista de producción
/operaciones/negocio NO usa este endpoint — lee de Mongo directo
(pobladado por jobs/negocio_movimientos.py).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import requests
from fastapi import APIRouter, HTTPException, Query

from api.services import aunesa_negocio as svc

router = APIRouter()
logger = logging.getLogger("api.manager.aunesa")


@router.get("/aunesa/explorar")
def aunesa_explorar(
    fecha: str | None = Query(None, description="YYYY-MM-DD; default: hoy ART"),
    tipos_cuenta: str = Query("Comitente", description="tiposCuenta param de Aunesa"),
) -> dict[str, Any]:
    """Pega a Aunesa LIVE, devuelve consolidado + raw + categorías.

    Para uso exploratorio desde /manager. La fecha se interpreta como ART.
    """
    if fecha:
        try:
            d = datetime.strptime(fecha, "%Y-%m-%d").date()
        except ValueError as e:
            raise HTTPException(status_code=400,
                                detail=f"fecha mal formada: {fecha} (esperado YYYY-MM-DD)") from e
    else:
        d = (datetime.now(UTC) - timedelta(hours=3)).date()

    try:
        return svc.fetch_y_consolidar(fecha=d, tipos_cuenta=tipos_cuenta)
    except requests.exceptions.Timeout as e:
        raise HTTPException(status_code=504, detail=str(e)) from e
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        body = e.response.text[:300] if e.response is not None else str(e)
        raise HTTPException(status_code=status, detail=f"Aunesa: {body}") from e
    except Exception as e:
        logger.exception("aunesa explorar failed")
        raise HTTPException(status_code=500, detail=str(e)) from e
