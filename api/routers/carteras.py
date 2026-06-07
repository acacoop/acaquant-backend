"""Router Portfolio — thin wrappers sobre `api.services.portfolio`.

La lógica (joins, agregaciones, valuación) vive en el service. Acá solo
declaramos endpoints FastAPI que parsean query params y delegan.

Scoping de grupos (Fase 2): cada endpoint resuelve el `scope` de cuentas
visibles del usuario (`scope_cuentas`) y lo pasa al service. `scope=None`
= sin restricción (admin o usuario sin grupo). Ver `docs/GRUPOS.md`.
"""
import os

from fastapi import APIRouter, Depends, HTTPException, Query

from api.services import pnl as pnl_svc
from api.services import portfolio as svc
from api.services import portfolio_sql as svc_sql
from api.services._grupos_scope import scope_cuentas, verificar_id_cuenta

router = APIRouter(prefix="/api/portfolio", tags=["Portfolio"])


def _psvc(engine: str | None):
    """Módulo de servicio de AuM: SQL o Mongo, por `?_engine=sql|mongo` o el flag
    global PORTFOLIO_SQL=1. Default Mongo. Solo aplica a las funciones ya migradas
    (listar_aum/cuentas, fci_*, total_*); tasa-fija/cer/pnl siguen en Mongo."""
    use_sql = engine == "sql" or (engine != "mongo" and os.getenv("PORTFOLIO_SQL") == "1")
    return svc_sql if use_sql else svc


def scope_aum(
    operador: str | None = Query(
        None, description="Filtro madre de la vista AUM: scopea TODO a las cuentas de ese operador (email)"
    ),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
) -> tuple[str, ...] | None:
    """Scope de cuentas de AUM = scope de grupos del usuario ∩ cuentas del operador.

    Sin `operador` → el scope de grupos tal cual (comportamiento actual). Con
    `operador`: si el user no está scopeado (admin, scope None) → las cuentas
    del operador; si está scopeado por grupo → la intersección. Reusa el mismo
    riel `scope` que ya aplican los services, así el filtro es "madre" sin tocar
    la lógica: estrechar el scope recalcula todas las tabs solas.
    """
    if not operador:
        return scope
    from api.services.comercial import _cuentas_de_operador
    op = _cuentas_de_operador(operador)
    if scope is None:
        return op
    return tuple(sorted(set(scope) & set(op)))


@router.get("/operadores")
def operadores():
    """Operadores para el filtro madre de la vista AUM (email, nombre, # cuentas)."""
    from api.services.comercial import listar_operadores_comercial
    return listar_operadores_comercial()


@router.get("/aum")
def listar_aum(
    id_cuenta: str | None = Query(None, description="Filtrar por id de cuenta"),
    unidad: str | None = Query(None, description="Filtrar por unidad/instrumento"),
    cuenta: str | None = Query(None, description="Filtrar por cuenta (formato [N] NOMBRE)"),
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    ultimo: bool = Query(False, description="Si true, devuelve solo el último snapshot"),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
    _engine: str | None = Query(None, include_in_schema=False),
):
    # Si se pide una cuenta puntual, verificar que esté dentro del scope.
    if id_cuenta and scope is not None and str(id_cuenta) not in scope:
        raise HTTPException(status_code=403, detail="no tenés acceso a esa cuenta")
    return _psvc(_engine).listar_aum(
        id_cuenta=id_cuenta, unidad=unidad, cuenta=cuenta,
        desde=desde, hasta=hasta, ultimo=ultimo, scope=scope,
    )


@router.get("/tasa-fija")
def tasa_fija_snapshot(scope: tuple[str, ...] | None = Depends(scope_aum)):
    return svc.tasa_fija_snapshot(scope=scope)


@router.get("/cer")
def cer_snapshot(scope: tuple[str, ...] | None = Depends(scope_aum)):
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
def listar_cuentas(scope: tuple[str, ...] | None = Depends(scope_cuentas),
                   _engine: str | None = Query(None, include_in_schema=False)):
    """Cuentas distintas del último snapshot AuM — para selectores. Limitado
    al scope de grupos del usuario."""
    return _psvc(_engine).listar_cuentas(scope=scope)


@router.get("/fci-serie")
def fci_serie(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    cuenta_filter: str = Query(
        "todas",
        description="Filtro de cuenta: todas | accionistas | sin_accionistas | cooperativas",
    ),
    scope: tuple[str, ...] | None = Depends(scope_aum),
    _engine: str | None = Query(None, include_in_schema=False),
):
    return _psvc(_engine).fci_serie(desde=desde, hasta=hasta, cuenta_filter=cuenta_filter,
                                    scope=scope)


@router.get("/fci-snapshot")
def fci_snapshot(
    fecha: str = Query(..., description="Fecha snapshot (YYYY-MM-DD)"),
    cuenta_filter: str = Query(
        "todas",
        description="Filtro de cuenta: todas | accionistas | sin_accionistas | cooperativas",
    ),
    scope: tuple[str, ...] | None = Depends(scope_aum),
    _engine: str | None = Query(None, include_in_schema=False),
):
    return _psvc(_engine).fci_snapshot(fecha=fecha, cuenta_filter=cuenta_filter, scope=scope)


@router.get("/total-serie")
def total_serie(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    cuenta_filter: str = Query(
        "todas",
        description="Filtro de cuenta: todas | accionistas | sin_accionistas | cooperativas",
    ),
    moneda: str = Query("ARS", description="ARS | USD — USD divide por MEP de cada fecha"),
    scope: tuple[str, ...] | None = Depends(scope_aum),
    _engine: str | None = Query(None, include_in_schema=False),
):
    """Serie histórica del AuM total agrupado por CARTERA."""
    return _psvc(_engine).total_serie(
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
    scope: tuple[str, ...] | None = Depends(scope_aum),
    _engine: str | None = Query(None, include_in_schema=False),
):
    """Diferencia de saldo por cuenta entre dos fechas snapshot."""
    return _psvc(_engine).total_diff(
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
    scope: tuple[str, ...] | None = Depends(scope_aum),
    _engine: str | None = Query(None, include_in_schema=False),
):
    """Snapshot del AuM total en una fecha (todas las unidades, by cartera)."""
    return _psvc(_engine).total_snapshot(
        fecha=fecha, cuenta_filter=cuenta_filter, moneda=moneda, scope=scope,
    )
