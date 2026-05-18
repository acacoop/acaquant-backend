"""Router Portfolio — thin wrappers sobre `api.services.portfolio`.

La lógica (joins, agregaciones, valuación) vive en el service. Acá solo
declaramos endpoints FastAPI que parsean query params y delegan.

Scoping de grupos (Fase 2): cada endpoint resuelve el `scope` de cuentas
visibles del usuario (`scope_cuentas`) y lo pasa al service. `scope=None`
= sin restricción (admin o usuario sin grupo). Ver `docs/GRUPOS.md`.
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from api.services import pnl as pnl_svc
from api.services import portfolio as svc
from api.services._grupos_scope import scope_cuentas, verificar_id_cuenta

router = APIRouter(prefix="/api/portfolio", tags=["Portfolio"])


@router.get("/aum")
def listar_aum(
    id_cuenta: str | None = Query(None, description="Filtrar por id de cuenta"),
    unidad: str | None = Query(None, description="Filtrar por unidad/instrumento"),
    cuenta: str | None = Query(None, description="Filtrar por cuenta (formato [N] NOMBRE)"),
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    ultimo: bool = Query(False, description="Si true, devuelve solo el último snapshot"),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
):
    # Si se pide una cuenta puntual, verificar que esté dentro del scope.
    if id_cuenta and scope is not None and str(id_cuenta) not in scope:
        raise HTTPException(status_code=403, detail="no tenés acceso a esa cuenta")
    return svc.listar_aum(
        id_cuenta=id_cuenta, unidad=unidad, cuenta=cuenta,
        desde=desde, hasta=hasta, ultimo=ultimo, scope=scope,
    )


@router.get("/tasa-fija")
def tasa_fija_snapshot(scope: tuple[str, ...] | None = Depends(scope_cuentas)):
    return svc.tasa_fija_snapshot(scope=scope)


@router.get("/cer")
def cer_snapshot(scope: tuple[str, ...] | None = Depends(scope_cuentas)):
    return svc.cer_snapshot(scope=scope)


@router.get("/pnl", dependencies=[Depends(verificar_id_cuenta)])
def pnl(id_cuenta: str = Query(..., description="id_cuenta numérico (ej '255')")):
    """PnL por (cuenta, ticker) basado en cash flows. Ver api.services.pnl
    para la lógica completa. `verificar_id_cuenta` corta con 403 si la
    cuenta está fuera del scope de grupos del usuario."""
    return pnl_svc.pnl_por_cuenta(id_cuenta=id_cuenta)


@router.get("/pnl-todas")
def pnl_todas(
    filtro_cuenta: str = Query(
        "todas",
        description="todas | accionistas | sin_accionistas | cooperativas | productores",
    ),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
):
    """PnL agregado de TODAS las cuentas — una fila por (cuenta, ticker).

    Útil para la sub-vista TOTALES en /aum → VALUACIONES. Itera sobre
    todas las cuentas del último snapshot y aplana los rows con info
    de cuenta (`cuenta`, `id_cuenta`). El `scope` de grupos limita el
    agregado a las cuentas visibles del usuario.

    Cacheada con TTL=60s.
    """
    return pnl_svc.pnl_todas_cuentas(filtro_cuenta=filtro_cuenta, scope=scope)


@router.get("/cuentas")
def listar_cuentas(scope: tuple[str, ...] | None = Depends(scope_cuentas)):
    """Cuentas distintas del último snapshot AuM — para selectores. Limitado
    al scope de grupos del usuario."""
    return svc.listar_cuentas(scope=scope)


@router.get("/fci-serie")
def fci_serie(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    cuenta_filter: str = Query(
        "todas",
        description="Filtro de cuenta: todas | accionistas | sin_accionistas | cooperativas",
    ),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
):
    return svc.fci_serie(desde=desde, hasta=hasta, cuenta_filter=cuenta_filter, scope=scope)


@router.get("/fci-snapshot")
def fci_snapshot(
    fecha: str = Query(..., description="Fecha snapshot (YYYY-MM-DD)"),
    cuenta_filter: str = Query(
        "todas",
        description="Filtro de cuenta: todas | accionistas | sin_accionistas | cooperativas",
    ),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
):
    return svc.fci_snapshot(fecha=fecha, cuenta_filter=cuenta_filter, scope=scope)


@router.get("/total-serie")
def total_serie(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    cuenta_filter: str = Query(
        "todas",
        description="Filtro de cuenta: todas | accionistas | sin_accionistas | cooperativas",
    ),
    moneda: str = Query("ARS", description="ARS | USD — USD divide por MEP de cada fecha"),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
):
    """Serie histórica del AuM total agrupado por CARTERA."""
    return svc.total_serie(
        desde=desde, hasta=hasta,
        cuenta_filter=cuenta_filter, moneda=moneda, scope=scope,
    )


@router.get("/diff")
def diff(
    fecha_actual:   str = Query(..., description="Fecha presente YYYY-MM-DD"),
    fecha_anterior: str = Query(..., description="Fecha contra la cual comparar YYYY-MM-DD"),
    moneda:         str = Query("ARS", description="ARS | USD"),
    cuenta_filter:  str = Query("todas",
                                description="todas | accionistas | sin_accionistas | cooperativas"),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
):
    """Diferencia de saldo por cuenta entre dos fechas snapshot."""
    return svc.total_diff(
        fecha_actual=fecha_actual,
        fecha_anterior=fecha_anterior,
        moneda=moneda,
        cuenta_filter=cuenta_filter,
        scope=scope,
    )


@router.get("/total-snapshot")
def total_snapshot(
    fecha: str = Query(..., description="Fecha snapshot (YYYY-MM-DD)"),
    cuenta_filter: str = Query(
        "todas",
        description="Filtro de cuenta: todas | accionistas | sin_accionistas | cooperativas",
    ),
    moneda: str = Query("ARS", description="ARS | USD — USD divide por MEP de la fecha"),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
):
    """Snapshot del AuM total en una fecha (todas las unidades, by cartera)."""
    return svc.total_snapshot(
        fecha=fecha, cuenta_filter=cuenta_filter, moneda=moneda, scope=scope,
    )
