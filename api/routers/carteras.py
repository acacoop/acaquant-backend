"""Router Portfolio — thin wrappers sobre `api.services.portfolio`.

La lógica (joins, agregaciones, valuación) vive en el service. Acá solo
declaramos endpoints FastAPI que parsean query params y delegan.
"""
from fastapi import APIRouter, Query

from api.services import portfolio as svc

router = APIRouter(prefix="/api/portfolio", tags=["Portfolio"])


@router.get("/carteras")
def listar_carteras(
    id_cuenta: str | None = Query(None, description="Filtrar por id de cuenta"),
    unidad: str | None = Query(None, description="Filtrar por unidad/instrumento"),
):
    return svc.listar_carteras(id_cuenta=id_cuenta, unidad=unidad)


@router.get("/aum")
def listar_aum(
    id_cuenta: str | None = Query(None, description="Filtrar por id de cuenta"),
    unidad: str | None = Query(None, description="Filtrar por unidad/instrumento"),
    cuenta: str | None = Query(None, description="Filtrar por cuenta (formato [N] NOMBRE)"),
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    ultimo: bool = Query(False, description="Si true, devuelve solo el último snapshot"),
):
    return svc.listar_aum(
        id_cuenta=id_cuenta, unidad=unidad, cuenta=cuenta,
        desde=desde, hasta=hasta, ultimo=ultimo,
    )


@router.get("/resumen")
def resumen_portfolio(
    id_cuenta: str | None = Query(None, description="id_cuenta para breakdown por cartera"),
):
    return svc.resumen_portfolio(id_cuenta=id_cuenta)


@router.get("/detalle")
def detalle_portfolio(
    id_cuenta: str = Query(..., description="id_cuenta de la cuenta a consultar"),
):
    return svc.detalle_portfolio(id_cuenta=id_cuenta)


@router.get("/tasa-fija")
def tasa_fija_snapshot():
    return svc.tasa_fija_snapshot()


@router.get("/cer")
def cer_snapshot():
    return svc.cer_snapshot()


@router.get("/cuentas")
def listar_cuentas():
    """Cuentas distintas del último snapshot AuM — para selectores."""
    return svc.listar_cuentas()


@router.get("/fci-serie")
def fci_serie(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    cuenta_filter: str = Query(
        "todas",
        description="Filtro de cuenta: todas | accionistas | sin_accionistas | cooperativas",
    ),
):
    return svc.fci_serie(desde=desde, hasta=hasta, cuenta_filter=cuenta_filter)


@router.get("/fci-snapshot")
def fci_snapshot(
    fecha: str = Query(..., description="Fecha snapshot (YYYY-MM-DD)"),
    cuenta_filter: str = Query(
        "todas",
        description="Filtro de cuenta: todas | accionistas | sin_accionistas | cooperativas",
    ),
):
    return svc.fci_snapshot(fecha=fecha, cuenta_filter=cuenta_filter)


@router.get("/total-serie")
def total_serie(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    cuenta_filter: str = Query(
        "todas",
        description="Filtro de cuenta: todas | accionistas | sin_accionistas | cooperativas",
    ),
    moneda: str = Query("ARS", description="ARS | USD — USD divide por MEP de cada fecha"),
):
    """Serie histórica del AuM total agrupado por CARTERA."""
    return svc.total_serie(
        desde=desde, hasta=hasta,
        cuenta_filter=cuenta_filter, moneda=moneda,
    )


@router.get("/diff")
def diff(
    fecha_actual:   str = Query(..., description="Fecha presente YYYY-MM-DD"),
    fecha_anterior: str = Query(..., description="Fecha contra la cual comparar YYYY-MM-DD"),
    moneda:         str = Query("ARS", description="ARS | USD"),
    cuenta_filter:  str = Query("todas",
                                description="todas | accionistas | sin_accionistas | cooperativas"),
):
    """Diferencia de saldo por cuenta entre dos fechas snapshot."""
    return svc.total_diff(
        fecha_actual=fecha_actual,
        fecha_anterior=fecha_anterior,
        moneda=moneda,
        cuenta_filter=cuenta_filter,
    )


@router.get("/total-snapshot")
def total_snapshot(
    fecha: str = Query(..., description="Fecha snapshot (YYYY-MM-DD)"),
    cuenta_filter: str = Query(
        "todas",
        description="Filtro de cuenta: todas | accionistas | sin_accionistas | cooperativas",
    ),
    moneda: str = Query("ARS", description="ARS | USD — USD divide por MEP de la fecha"),
):
    """Snapshot del AuM total en una fecha (todas las unidades, by cartera)."""
    return svc.total_snapshot(
        fecha=fecha, cuenta_filter=cuenta_filter, moneda=moneda,
    )
