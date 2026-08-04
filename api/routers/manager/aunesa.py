"""Manager · Aunesa — endpoints exploratorios + backfill de aranceles.

Thin HTTP plumbing. La lógica vive en services:
- `api/services/aunesa_negocio.py` (explorar en vivo),
- `api/services/aranceles_jobs.py` (tab FALTANTES + runner del backfill).

La vista de producción /operaciones/negocio NO usa estos endpoints — lee de
SQL operaciones.negocio_movimientos (escrita por jobs/negocio_movimientos.py).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import requests
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services import aranceles_jobs
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
        description="Fecha YYYY-MM-DD que se manda como `desde` a Aunesa. Por la "
                    "regla H1, `desde=X` devuelve la posición del día hábil "
                    "ANTERIOR a X. Default: hoy (= último cierre hábil).",
    ),
) -> dict[str, Any]:
    """Pega a Aunesa LIVE y devuelve la posición valuada CRUDA de una cuenta.

    NO pasa por el AuM — es la respuesta directa del endpoint `posicionValuada`
    de Aunesa. Sirve para comparar lo que Aunesa manda contra lo que el job
    terminó persistiendo. `cantidad` viene con el signo nativo de Aunesa.

    Devuelve solo los items con `informacion == "Acumulado"` (las posiciones).
    """
    # Import adentro: el cliente Aunesa vive en jobs/aum.py. Si fallara el
    # import, solo se cae este endpoint — no el resto de la API.
    try:
        from jobs.aum import autenticar, consultar_posicion
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
        # Regla H1: desde=hoy → Aunesa devuelve el último cierre hábil.
        desde_q = datetime.now().strftime("%d/%m/%Y")
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


@router.get("/aunesa/boletos/faltantes")
def aunesa_boletos_faltantes(
    desde:    str        = Query(..., description="Concertación desde YYYY-MM-DD"),
    hasta:    str        = Query(..., description="Concertación hasta YYYY-MM-DD"),
    id_cuenta: str | None = Query(None, description="Restringir a una cuenta (id)"),
    limit:    int        = Query(2000, ge=1, le=20000, description="Tope filas devueltas"),
) -> dict[str, Any]:
    """Boletos sin arancel en el rango (tab FALTANTES). Lógica en aranceles_jobs."""
    try:
        return aranceles_jobs.boletos_faltantes(
            desde=desde, hasta=hasta, id_cuenta=id_cuenta, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


class BackfillReq(BaseModel):
    desde:   str        = Field(..., description="Concertación desde YYYY-MM-DD")
    hasta:   str        = Field(..., description="Concertación hasta YYYY-MM-DD")
    cuentas: list[str] | None = Field(
        default=None,
        description="Restringir a estas cuentas (id_cuenta). None = todas las del rango.",
    )
    workers: int        = Field(6, ge=1, le=20, description="Threads paralelos contra Aunesa.")
    apply:   bool       = Field(True, description="True = escribe; False = dry-run.")


@router.post("/aunesa/boletos/backfill")
def boletos_backfill_start(
    req: BackfillReq = Body(...),
    actor: str = Depends(get_user_email),
) -> dict[str, Any]:
    """Arranca el backfill de aranceles en background. Devuelve `job_id`.

    El frontend hace polling de `GET /aunesa/boletos/backfill/{job_id}`.
    Runner + persistencia en api/services/aranceles_jobs.py.
    """
    try:
        return aranceles_jobs.iniciar_backfill(
            desde=req.desde, hasta=req.hasta, cuentas=req.cuentas,
            workers=req.workers, apply=req.apply, actor=actor)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/aunesa/boletos/backfill/{job_id}")
def boletos_backfill_status(job_id: str) -> dict[str, Any]:
    """Estado actual del job. Marca `stale` si lleva > 5 min sin update."""
    try:
        return aranceles_jobs.estado_job(job_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/aunesa/boletos/backfill")
def boletos_backfill_historial(limit: int = Query(20, ge=1, le=100)) -> list[dict[str, Any]]:
    """Últimos N jobs (más reciente primero). Para mostrar historial en UI."""
    return aranceles_jobs.historial_jobs(limit=limit)
