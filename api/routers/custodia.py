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
    fuente: str = Query("t0", description="Contra qué se compara Aunesa: 't0' o 'cierre'."),
) -> dict:
    """La foto de la Caja de Valores de un día, entera, con sus contadores.

    UN request trae todo lo que dibuja la tab. Los filtros (cuenta, estado,
    trabado, sin instrumento) los aplica la pantalla sobre estas mismas filas:
    la foto de un día es un conjunto cerrado, así que filtrar en memoria es
    instantáneo y los contadores no pueden contradecir a la lista.

    `fuente` SÍ va al backend porque cambia de qué tabla sale la comparación:
    't0' (liquidada a hoy, para la conciliación nocturna) o 'cierre' (la foto
    conciliada, comparable con BYMA durante el día, que actualiza tras las 21).
    """
    return custodia_sql.tenencias(fecha=fecha, fuente=fuente)


@router.get("/movimientos")
def movimientos(
    fecha: date | None = Query(None, description="YYYY-MM-DD. Default: la última con datos."),
    dias: int = Query(1, ge=1, le=30, description="Ventana hacia atrás desde `fecha`."),
) -> dict:
    """Las liquidaciones de custodia, **plegadas**: una fila por movimiento.

    La tabla guarda PATAS (partida doble: cada instrucción viene dos veces, con
    volumen de signo opuesto, una por cada cuenta). Acá salen ya plegadas en un
    movimiento con `entrega` / `recibe`: devolver las patas crudas sería
    devolver cada movimiento duplicado, y el front de esta app no deriva nada.

    `dias` existe porque el feed corre varias veces al día y una liquidación
    puede aparecer tarde: pedir solo hoy a las 9 devuelve vacío y eso no
    significa que no haya habido movimientos.
    """
    return custodia_sql.movimientos(fecha=fecha, dias=dias)
