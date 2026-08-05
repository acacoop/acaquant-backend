"""Router Portfolio — thin wrappers sobre `api.services.portfolio`.

La lógica (joins, agregaciones, valuación) vive en el service. Acá solo
declaramos endpoints FastAPI que parsean query params y delegan.

Scoping de grupos (Fase 2): cada endpoint resuelve el `scope` de cuentas
visibles del usuario (`scope_cuentas`) y lo pasa al service. `scope=None`
= sin restricción (admin o usuario sin grupo). Ver `docs/GRUPOS.md`.
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from api.services import pnl_sql
from api.services import portfolio_sql as svc_sql
from api.services._grupos_scope import scope_cuentas, verificar_id_cuenta

router = APIRouter(prefix="/api/portfolio", tags=["Portfolio"])


def scope_aum(
    operador: str | None = Query(
        None, description="Filtro madre de la vista AUM: scopea TODO a las cuentas de ese operador (email)"
    ),
    nivel_1: str | None = Query(
        None, description="Filtro madre de la vista AUM: scopea a las cuentas de ese Nivel 1 (segmento)"
    ),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
) -> tuple[str, ...] | None:
    """Scope de cuentas de AUM = scope de grupos del usuario ∩ cuentas del operador ∩ nivel_1.

    Sin `operador` ni `nivel_1` → el scope de grupos tal cual (comportamiento actual).
    Con alguno: si el user no está scopeado (admin, scope None) → las cuentas del
    filtro; si está scopeado por grupo → la intersección. Reusa el mismo riel `scope`
    que ya aplican los services, así el filtro es "madre" sin tocar la lógica:
    estrechar el scope recalcula todas las tabs solas.
    """
    if not operador and not nivel_1:
        return scope
    from api.services.comercial import TODOS, _cuentas_de_operador
    # `_cuentas_de_operador` ya combina operador + nivel_1 (AND) en una sola query.
    sel = _cuentas_de_operador(operador or TODOS, nivel_1=nivel_1)
    if scope is None:
        return sel
    return tuple(sorted(set(scope) & set(sel)))


@router.get("/niveles-1")
def niveles_1() -> dict:
    """Valores distintos de nivel_1 (segmento) de comitentes activas, para el filtro
    madre de la vista AUM."""
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT nivel_1 FROM clientes.comitentes "
                    "WHERE nivel_1 IS NOT NULL AND nivel_1 <> '' AND estado = 'Activa' "
                    "ORDER BY nivel_1")
        return {"niveles_1": [r[0] for r in cur.fetchall()]}


@router.get("/operadores")
def operadores():
    """Operadores para el filtro madre de la vista AUM (email, nombre, # cuentas)."""
    from api.services.comercial_sql import listar_operadores_comercial
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
):
    # Si se pide una cuenta puntual, verificar que esté dentro del scope.
    if id_cuenta and scope is not None and str(id_cuenta) not in scope:
        raise HTTPException(status_code=403, detail="no tenés acceso a esa cuenta")
    return svc_sql.listar_aum(
        id_cuenta=id_cuenta, unidad=unidad, cuenta=cuenta,
        desde=desde, hasta=hasta, ultimo=ultimo, scope=scope,
    )


@router.get("/pnl", dependencies=[Depends(verificar_id_cuenta)])
def pnl(id_cuenta: str = Query(..., description="id_cuenta numérico (ej '255')")):
    """PnL por (cuenta, ticker) basado en cash flows — SIEMPRE SQL (`pnl_sql`,
    cost-basis sobre Postgres). La rama Mongo (`pnl.pnl_por_cuenta`) se RETIRÓ
    (decommission 2026-06-22). `verificar_id_cuenta` corta con 403 si la cuenta
    está fuera del scope de grupos del usuario."""
    return pnl_sql.pnl_por_cuenta_sql(id_cuenta=id_cuenta)


@router.get("/pnl-todas")
def pnl_todas(
    filtro_cuenta: str = Query(
        "todas",
        description="todas | accionistas | sin_accionistas | cooperativas | productores",
    ),
    scope: tuple[str, ...] | None = Depends(scope_cuentas),
):
    """PnL agregado de TODAS las cuentas — una fila por (cuenta, ticker).

    Útil para la sub-vista TOTALES en /aum → VALUACIONES. Lee el cache precalculado
    (cron jobs.pnl_totales_precompute) y aplana los rows con info de cuenta (`cuenta`,
    `id_cuenta`). El `scope` de grupos limita el agregado a las cuentas visibles del
    usuario.

    SQL-native (decomiso Mongo): lee `valuaciones.pnl_totales_cache`. Valuaciones.PnLTotalesCache
    (Mongo) dropeada → el gemelo pnl.py ya no se usa."""
    return pnl_sql.pnl_todas_cuentas_sql(filtro_cuenta=filtro_cuenta, scope=scope)


@router.get("/cuentas")
def listar_cuentas(scope: tuple[str, ...] | None = Depends(scope_cuentas)):
    """Cuentas distintas del último snapshot AuM — para selectores. Limitado
    al scope de grupos del usuario."""
    return svc_sql.listar_cuentas(scope=scope)


@router.get("/fci-serie")
def fci_serie(
    desde: str | None = Query(None, description="Fecha desde (YYYY-MM-DD)"),
    hasta: str | None = Query(None, description="Fecha hasta (YYYY-MM-DD)"),
    cuenta_filter: str = Query(
        "todas",
        description="Filtro de cuenta: todas | accionistas | sin_accionistas | cooperativas",
    ),
    scope: tuple[str, ...] | None = Depends(scope_aum),
):
    return svc_sql.fci_serie(desde=desde, hasta=hasta, cuenta_filter=cuenta_filter,
                                    scope=scope)


@router.get("/fci-snapshot")
def fci_snapshot(
    fecha: str = Query(..., description="Fecha snapshot (YYYY-MM-DD)"),
    cuenta_filter: str = Query(
        "todas",
        description="Filtro de cuenta: todas | accionistas | sin_accionistas | cooperativas",
    ),
    scope: tuple[str, ...] | None = Depends(scope_aum),
):
    return svc_sql.fci_snapshot(fecha=fecha, cuenta_filter=cuenta_filter, scope=scope)


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
):
    """Serie histórica del AuM total agrupado por CARTERA."""
    return svc_sql.total_serie(
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
):
    """Diferencia de saldo por cuenta entre dos fechas snapshot."""
    return svc_sql.total_diff(
        fecha_actual=fecha_actual,
        fecha_anterior=fecha_anterior,
        moneda=moneda,
        cuenta_filter=cuenta_filter,
        scope=scope,
    )


@router.get("/total-snapshot")
def total_snapshot(
    fecha: str | None = Query(None, description="Fecha snapshot (YYYY-MM-DD); ausente = última disponible"),
    cuenta_filter: str = Query(
        "todas",
        description="Filtro de cuenta: todas | accionistas | sin_accionistas | cooperativas",
    ),
    moneda: str = Query("ARS", description="ARS | USD — USD divide por MEP de la fecha"),
    scope: tuple[str, ...] | None = Depends(scope_aum),
):
    """Snapshot del AuM total en una fecha (todas las unidades, by cartera)."""
    return svc_sql.total_snapshot(
        fecha=fecha, cuenta_filter=cuenta_filter, moneda=moneda, scope=scope,
    )
