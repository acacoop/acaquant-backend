"""Manager · Aunesa — endpoint exploratorio en vivo.

Thin wrapper sobre `api/services/aunesa_negocio.py`. Para discovery /
debugging desde el panel /manager. La vista de producción
/operaciones/negocio NO usa este endpoint — lee de Mongo directo
(pobladado por jobs/negocio_movimientos.py).
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import requests
from fastapi import APIRouter, HTTPException, Query

from api.services import aunesa_negocio as svc
from api.services._negocio_futuros import match_no_futuros
from core.mongo import get_mongo_client_read

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


# ── BOLETOS → tab FALTANTES ───────────────────────────────────────────────────
# Lista los boletos en CashFlow.NegocioMovimientos del rango pedido que NO
# tienen arancel (campo `arancel` ausente o ≤ 0). Sirve para detectar GAPs
# antes/después de correr el backfill. Filtra futuros DLR (unidad=USDL) —
# no tienen arancel propio y no entrarían igual.

_FALTANTES_PROJ = {
    "_id": 0, "comprobante": 1, "id_cuenta": 1, "cuenta": 1, "denominacion": 1,
    "fecha": 1, "categoria": 1, "moneda": 1, "ticker": 1, "unidad": 1,
    "importe": 1, "arancel": 1,
}


@router.get("/aunesa/boletos/faltantes")
def aunesa_boletos_faltantes(
    desde:    str        = Query(..., description="Concertación desde YYYY-MM-DD"),
    hasta:    str        = Query(..., description="Concertación hasta YYYY-MM-DD"),
    id_cuenta: str | None = Query(None, description="Restringir a una cuenta (id)"),
    limit:    int        = Query(2000, ge=1, le=20000, description="Tope filas devueltas"),
) -> dict[str, Any]:
    """Boletos sin arancel en el rango. Devuelve filas + agregado por cuenta+día.

    Considera "sin arancel": `arancel` no existe, es null, o ≤ 0. Match por
    `fecha` (string YYYY-MM-DD, índice). Excluye futuros DLR (USDL)
    automáticamente — los futuros no llevan arancel del proyecto.
    """
    try:
        date.fromisoformat(desde)
        date.fromisoformat(hasta)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"fecha mal formada: {e}") from e
    if desde > hasta:
        raise HTTPException(status_code=400, detail="desde > hasta")

    coll = get_mongo_client_read()["CashFlow"]["NegocioMovimientos"]
    match: dict[str, Any] = {
        "fecha": {"$gte": desde, "$lte": hasta},
        "$or": [{"arancel": {"$exists": False}}, {"arancel": {"$lte": 0}}, {"arancel": None}],
        **match_no_futuros(),
    }
    if id_cuenta:
        match["id_cuenta"] = str(id_cuenta)

    # Boletos detallados (capped).
    boletos = list(coll.find(match, _FALTANTES_PROJ).sort([("fecha", -1)]).limit(limit))

    # Agregado por (id_cuenta, fecha) sobre TODO el rango — sin cap, para que
    # el resumen sea fiel aunque la tabla detallada esté truncada.
    por_cuenta_fecha = list(coll.aggregate([
        {"$match": match},
        {"$group": {
            "_id":          {"id_cuenta": "$id_cuenta", "fecha": "$fecha"},
            "n":            {"$sum": 1},
            "denominacion": {"$first": "$denominacion"},
            "importe_abs":  {"$sum": {"$abs": {"$ifNull": ["$importe", 0]}}},
        }},
        {"$sort": {"_id.fecha": -1, "_id.id_cuenta": 1}},
    ]))

    n_total = sum(int(r["n"]) for r in por_cuenta_fecha)
    return {
        "desde": desde, "hasta": hasta, "id_cuenta": id_cuenta,
        "n_total":   n_total,
        "truncado":  len(boletos) >= limit,
        "limit":     limit,
        "resumen": [
            {
                "id_cuenta":    r["_id"]["id_cuenta"],
                "fecha":        r["_id"]["fecha"],
                "denominacion": r.get("denominacion"),
                "n":            int(r["n"]),
                "importe_abs":  float(r.get("importe_abs") or 0.0),
            }
            for r in por_cuenta_fecha
        ],
        "boletos": boletos,
    }
