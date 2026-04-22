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


@router.get("/fci-serie")
def fci_serie(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
):
    return svc.fci_serie(desde=desde, hasta=hasta)


@router.get("/fci-snapshot")
def fci_snapshot(fecha: str = Query(..., description="Fecha snapshot (YYYY-MM-DD)")):
    return svc.fci_snapshot(fecha=fecha)
