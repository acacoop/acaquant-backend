"""Router /api/valuaciones — performance e historia por cuenta.

Endpoints principales (AUM-based, vista actual):
- GET /{id_cuenta}/vista    — el INFORME completo (resumen + activos + métricas).
- GET /{id_cuenta}/serie    — daily portfolio total (Valuaciones.AuM).
- GET /{id_cuenta}/mensual  — cierre mensual + flujos externos.

Endpoint legacy (cost-basis ledger, para drill-down per-ticker en Phase 2):
- GET /{id_cuenta}/posiciones — weighted-avg cost + PnL realizado/no real.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from api.services import valuaciones as svc
from api.services._grupos_scope import scope_cuentas, verificar_id_cuenta

logger = logging.getLogger("api.valuaciones")

router = APIRouter(prefix="/api/valuaciones", tags=["Valuaciones"])


def _validate_id_cuenta(id_cuenta: str) -> None:
    if not id_cuenta or not id_cuenta.isdigit():
        raise HTTPException(400, f"id_cuenta inválida: {id_cuenta!r}")


@router.get("/consolidado")
def get_consolidado(
    filtro_cuenta: str = Query(
        "todas",
        description="todas | accionistas | sin_accionistas | cooperativas | productores",
    ),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
):
    """Una fila por cuenta: valor, base 100, PnL acum, TEM, TEA (ARS y USD).

    Reusa el cálculo de la tabla MENSUAL de PORTAFOLIO, consolidado para
    comparar carteras entre sí. Cacheado — el primer load puede tardar.
    El `scope` de grupos limita las filas a las cuentas visibles del user.
    """
    try:
        from api.services import valuaciones_sql as svc_sql
        return svc_sql.valuacion_consolidada(filtro_cuenta=filtro_cuenta, scope=scope)
    except Exception as e:
        logger.exception("valuaciones consolidado failed: filtro=%s", filtro_cuenta)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{id_cuenta}/serie", dependencies=[Depends(verificar_id_cuenta)])
def get_serie(
    id_cuenta: str,
    desde: str | None = Query(None, description="YYYY-MM-DD inclusive"),
    hasta: str | None = Query(None, description="YYYY-MM-DD inclusive"),
):
    """Serie diaria del valor total del portfolio (Valuaciones.AuM)."""
    _validate_id_cuenta(id_cuenta)
    try:
        from api.services import valuaciones_sql as svc_sql
        return svc_sql.serie_valor_cuenta(id_cuenta=id_cuenta, desde=desde, hasta=hasta)
    except Exception as e:
        logger.exception(
            "valuaciones serie failed: id_cuenta=%s desde=%s hasta=%s",
            id_cuenta, desde, hasta,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{id_cuenta}/mensual", dependencies=[Depends(verificar_id_cuenta)])
def get_mensual(id_cuenta: str):
    """Tabla mensual: cierre del mes (último fecha_snapshot) +
    flujos externos del mes (depósitos − extracciones)."""
    _validate_id_cuenta(id_cuenta)
    try:
        return svc.valuacion_mensual(id_cuenta=id_cuenta)
    except Exception as e:
        logger.exception("valuaciones mensual failed: id_cuenta=%s", id_cuenta)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{id_cuenta}/movimientos", dependencies=[Depends(verificar_id_cuenta)])
def get_movimientos(
    id_cuenta: str,
    fecha: str = Query(..., description="YYYY-MM-DD — define el mes a consultar"),
):
    """Movimientos individuales (depósitos, extracciones, transferencias)
    para la cuenta en el mes que contiene `fecha`. Para auditoría en
    /valuaciones — al clickear un mes ves cada boleto con su fecha real."""
    _validate_id_cuenta(id_cuenta)
    try:
        from datetime import datetime
        datetime.strptime(fecha, "%Y-%m-%d")
    except ValueError as e:
        raise HTTPException(400, f"fecha mal formada: {fecha!r}") from e
    try:
        return svc.movimientos_mes(id_cuenta=id_cuenta, fecha_anchor=fecha)
    except Exception as e:
        logger.exception(
            "valuaciones movimientos failed: id_cuenta=%s fecha=%s",
            id_cuenta, fecha,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{id_cuenta}/variacion", dependencies=[Depends(verificar_id_cuenta)])
def get_variacion(
    id_cuenta: str,
    fecha: str = Query(
        ...,
        description="YYYY-MM-DD — fecha_snapshot del mes; se compara contra "
                    "el snapshot anterior.",
    ),
):
    """Descompone la variación del portfolio vs el snapshot anterior, por
    título, separando efecto mercado (precio) de efecto operado (cantidad).
    El efectivo se agrupa en 'otros'."""
    _validate_id_cuenta(id_cuenta)
    try:
        from datetime import datetime
        datetime.strptime(fecha, "%Y-%m-%d")
    except ValueError as e:
        raise HTTPException(400, f"fecha mal formada: {fecha!r}") from e
    try:
        from api.services import valuaciones_sql as svc_sql
        return svc_sql.variacion_titulos(id_cuenta=id_cuenta, fecha=fecha)
    except Exception as e:
        logger.exception(
            "valuaciones variacion failed: id_cuenta=%s fecha=%s",
            id_cuenta, fecha,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{id_cuenta}/vista", dependencies=[Depends(verificar_id_cuenta)])
def get_vista(
    id_cuenta: str,
    fecha: str | None = Query(
        None,
        description="YYYY-MM-DD. Si se omite, la posición de HOY.",
    ),
    horizonte: str = Query(
        "t1",
        description="Solo SIN `fecha`: t1 (default) = con lo concertado hoy adentro · "
                    "t0 = liquidada a hoy.",
    ),
):
    """El INFORME de la cuenta en UN request: RESUMEN + ACTIVOS + MÉTRICAS.

    Es lo que sirve la vista NEGOCIO → CARTERAS. Antes eran cuatro llamadas y los
    totales se armaban en el navegador; con la fórmula duplicada del lado del
    front la pantalla podía contradecir al informe. Además el PDF sale de ESTE
    payload, así que no puede decir algo distinto de lo que se ve en pantalla.

    Ver `api/services/carteras_informe.py`.
    """
    _validate_id_cuenta(id_cuenta)
    if fecha:
        try:
            from datetime import datetime
            datetime.strptime(fecha, "%Y-%m-%d")
        except ValueError as e:
            raise HTTPException(400, f"fecha mal formada: {fecha!r}") from e
    try:
        from api.services import carteras_informe
        return carteras_informe.vista(id_cuenta=id_cuenta, fecha=fecha,
                                      horizonte=horizonte)
    except Exception as e:
        logger.exception("valuaciones vista failed: id_cuenta=%s fecha=%s",
                         id_cuenta, fecha)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{id_cuenta}/posiciones-actuales", dependencies=[Depends(verificar_id_cuenta)])
def get_posiciones_actuales(
    id_cuenta: str,
    fecha: str | None = Query(
        None,
        description="YYYY-MM-DD. Si se omite, usa el último fecha_snapshot.",
    ),
    con_pnl: bool = Query(
        False,
        description="Adjunta cost-basis y PnL por título (solo si la fecha es el "
                    "último snapshot). Cuesta una corrida del motor de PnL.",
    ),
    horizonte: str = Query(
        "t1",
        description="Solo SIN `fecha` (modo actual): t1 = posición con lo concertado "
                    "HOY adentro (default, la que mira el negocio) · t0 = liquidada a "
                    "hoy, lo que está en custodia (la que mira el back office). "
                    "Con `fecha` se ignora: un día pasado ya liquidó todo.",
    ),
):
    """Posiciones de un fecha_snapshot dado — por default, el más
    reciente. Pasar fecha=YYYY-MM-DD para ver una fecha histórica
    (driven por el click en la tabla mensual de /valuaciones).

    Pure AuM read; con `con_pnl=true` suma costo/PnL/gan% por título y el
    detalle (boletos) que consume el panel de AUDITORÍA de CARTERAS.

    SIN `fecha` la posición sale de `portafolio.tenencia_live` (el daemon
    `jobs.tenencia_live`); CON `fecha`, de la foto conciliada de siempre.
    """
    _validate_id_cuenta(id_cuenta)
    if fecha:
        # Cheap shape validation — Mongo stores fechas como strings.
        try:
            from datetime import datetime
            datetime.strptime(fecha, "%Y-%m-%d")
        except ValueError as e:
            raise HTTPException(400, f"fecha mal formada: {fecha!r}") from e
    try:
        from api.services import valuaciones_sql as svc_sql
        return svc_sql.posiciones_actuales(
            id_cuenta=id_cuenta, fecha=fecha, asof=True, con_pnl=con_pnl,
            horizonte=horizonte,
        )
    except Exception as e:
        logger.exception(
            "valuaciones posiciones-actuales failed: id_cuenta=%s fecha=%s",
            id_cuenta, fecha,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{id_cuenta}/posiciones", dependencies=[Depends(verificar_id_cuenta)])
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
