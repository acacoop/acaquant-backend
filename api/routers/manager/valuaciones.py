"""Manager · Valuaciones — debug XIRR mensual.

Endpoint exploratorio para auditar paso por paso cómo se llega a la TEA
mensual y a la base 100 cumulada. Útil para:
  - Validar los flujos del mes (¿el sistema vio el depósito del día X?).
  - Reproducir el cálculo en Excel con TIR.NO.PER.
  - Detectar meses con XIRR no convergente (te dice cuál es el cashflow
    degenerado).

Thin wrapper sobre `api.services.valuaciones.valuacion_mensual_debug`,
que NO está cacheado — siempre muestra datos actuales.

Acceso: admin (el router padre `api/routers/manager/__init__.py` ya
exige `require_module('manager')`).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from api.services import valuaciones as svc

router = APIRouter()
logger = logging.getLogger("api.manager.valuaciones")


@router.get("/valuaciones/debug")
def valuaciones_debug(
    id_cuenta: str = Query(..., description="ID numérico de cuenta, ej '805'"),
    _engine: str | None = Query(None, include_in_schema=False),
) -> dict[str, Any]:
    """Devuelve el desglose mes-por-mes del cálculo de XIRR.

    Por cada mes incluye:
      - fecha_inicio / fecha_cierre del período (último día con snapshot
        del mes anterior y del mes corriente)
      - valor_inicio / valor_cierre (saldo del portfolio en cada fecha)
      - flujos_individuales: cada doc de NegocioMovimientos del mes con
        importe original + MEP aplicado + importe ARS
      - cashflow_xirr: lista exacta de (fecha, monto) que recibió la
        función xirr — pegable directo en Excel TIR.NO.PER
      - tea_mensual: resultado de XIRR (TEA anualizada)
      - dias_periodo, tem_periodo: TEA des-anualizada al período exacto
      - twr_base100_acum: base 100 cumulada al cierre del mes

    Mes más reciente primero.
    """
    try:
        return svc.valuacion_mensual_debug(id_cuenta=id_cuenta, engine="sql")
    except Exception as e:
        logger.exception("valuaciones_debug failed para %s", id_cuenta)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/aum")
def get_aum(
    id_cuenta: str = Query(..., description="ID numérico de cuenta, ej '805'"),
    fecha: str | None = Query(
        None,
        description="fecha_snapshot YYYY-MM-DD. Si se omite, el último disponible.",
    ),
) -> dict[str, Any]:
    """Docs crudos de Valuaciones.AuM para una (cuenta, fecha).

    Devuelve cada posición tal cual está en la colección — unidad,
    cantidad, precio, valuacion — más `valuacion_esperada` (cantidad ×
    precio) y `desvio` para detectar a ojo precios mal traídos. Incluye
    `fechas_disponibles` para poblar el selector de fecha del frontend.
    """
    try:
        return svc.aum_raw(id_cuenta=id_cuenta, fecha=fecha)
    except Exception as e:
        logger.exception("get_aum failed para %s / %s", id_cuenta, fecha)
        raise HTTPException(status_code=500, detail=str(e)) from e
