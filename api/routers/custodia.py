"""api/routers/custodia.py — CUSTODIA (CVSA). Solo HTTP; la lógica está en services.

Se monta con `_BACK_OFFICE` en `api/main.py`: módulo `back-office`, o sea admin,
trader, sales, asistente_comercial y back_office. **NUNCA invitado** (REGLA #8):
es tenencia de clientes, negocio de la mesa puro.

Todo de LECTURA: no hay un solo endpoint que escriba.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from api.services import custodia_sql

# Prefijo `/api/back-office/custodia`, igual que senebis e interbanking: así el
# gate de los dos repos (ENDPOINT_MODULE_PREFIXES y PATH_MODULES) ya lo cubre
# por el prefijo `/api/back-office`, y no hay un contrato más que replicar a
# mano — que es justo donde los dos mapas se desincronizan sin que falle nada.
router = APIRouter(prefix="/api/back-office/custodia", tags=["Custodia"])


@router.get("/tenencias")
def tenencias(
    fecha: date | None = Query(None, description="YYYY-MM-DD. Default: la última foto."),
    id_cuenta: str | None = Query(None, description="Filtrar por cuenta ('805')."),
    estado: str | None = Query(None, description="subBalanceType exacto (AVAILABLE, EMBARGO…)."),
    solo_trabado: bool = Query(False, description="Solo lo que NO está disponible."),
) -> dict:
    """La tenencia según la Caja de Valores, con sus contadores.

    UN request trae todo lo que dibuja la tab: filas, totales, frescura y el
    universo de estados. El front no suma nada.
    """
    datos = custodia_sql.tenencias(fecha=fecha, id_cuenta=id_cuenta,
                                   estado=estado, solo_trabado=solo_trabado)
    datos["estados"] = custodia_sql.estados(
        date.fromisoformat(datos["fecha"]) if datos.get("fecha") else None)
    return datos
