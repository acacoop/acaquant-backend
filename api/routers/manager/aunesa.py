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


@router.get("/aunesa/posicion")
def aunesa_posicion(
    id_cuenta: str = Query(..., description="ID de cuenta Aunesa, ej '805'"),
    desde: str | None = Query(
        None,
        description="Fecha de liquidación YYYY-MM-DD. Default: T+2 hábil "
                    "(igual que el job jobs/aum.py).",
    ),
) -> dict[str, Any]:
    """Pega a Aunesa LIVE y devuelve la posición valuada CRUDA de una cuenta.

    NO pasa por Valuaciones.AuM — es la respuesta directa del endpoint
    `posicionValuada` de Aunesa. Sirve para comparar lo que Aunesa manda
    (precio, cantidad por unidad) contra lo que el job terminó persistiendo
    en la base. `cantidad` viene con el signo nativo de Aunesa (el job la
    invierte al procesar).

    Devuelve solo los items con `informacion == "Acumulado"` (las
    posiciones; el resto de la respuesta son detalles intermedios).
    """
    # Import adentro: el cliente Aunesa vive en jobs/aum.py. Si fallara el
    # import, solo se cae este endpoint — no el resto de la API.
    try:
        from jobs.aum import autenticar, consultar_posicion, fecha_t2
    except Exception as e:
        logger.exception("no se pudo importar el cliente Aunesa de jobs.aum")
        raise HTTPException(status_code=500,
                            detail=f"import cliente Aunesa: {e}") from e

    # Aunesa espera la fecha en DD/MM/YYYY. El frontend manda YYYY-MM-DD.
    if desde:
        try:
            desde_q = datetime.strptime(desde, "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail=f"fecha mal formada: {desde} (esperado YYYY-MM-DD)",
            ) from e
    else:
        desde_q = fecha_t2()
    try:
        headers = autenticar()
        data, necesita_reauth = consultar_posicion(id_cuenta, headers, desde_q)
        if necesita_reauth:
            headers = autenticar()
            data, _ = consultar_posicion(id_cuenta, headers, desde_q)
    except requests.exceptions.Timeout as e:
        raise HTTPException(status_code=504, detail=f"Aunesa timeout: {e}") from e
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        body = e.response.text[:300] if e.response is not None else str(e)
        raise HTTPException(status_code=status, detail=f"Aunesa: {body}") from e
    except Exception as e:
        logger.exception("aunesa_posicion failed para %s", id_cuenta)
        raise HTTPException(status_code=500, detail=str(e)) from e

    if data is None:
        raise HTTPException(status_code=502,
                            detail="Aunesa devolvió respuesta vacía / no-200")

    items = data if isinstance(data, list) else []
    acumulado = [r for r in items if r.get("informacion") == "Acumulado"]
    return {
        "id_cuenta":   id_cuenta,
        "desde":       desde_q,
        "n_total":     len(items),
        "n_acumulado": len(acumulado),
        "posiciones":  acumulado,
    }
