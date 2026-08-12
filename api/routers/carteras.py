"""Router Portfolio — thin wrappers sobre `api.services.portfolio`.

La lógica (joins, agregaciones, valuación) vive en el service. Acá solo
declaramos endpoints FastAPI que parsean query params y delegan.

Scoping de grupos (Fase 2): cada endpoint resuelve el `scope` de cuentas
visibles del usuario (`scope_cuentas`) y lo pasa al service. `scope=None`
= sin restricción (admin o usuario sin grupo). Ver `docs/GRUPOS.md`.
"""
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services import pnl_ajustes_sql, pnl_sql
from api.services import portfolio_sql as svc_sql
from api.services._grupos_scope import scope_cuentas, verificar_id_cuenta

router = APIRouter(prefix="/api/portfolio", tags=["Portfolio"])


def require_escritura_ajustes(actor: str = Depends(get_user_email)) -> str:
    """Escritura de ajustes de PnL: admin (todo, incluidos globales) o un
    OPERADOR COMERCIAL con cuentas asignadas (solo ajustes de SUS cuentas —
    ownership por clientes.comitentes.operador_email). Este gate es el grueso;
    el alcance fino por cuenta lo valida el service (`_verificar_alcance`) en
    cada write. Va como DEPENDENCY, no como chequeo dentro del handler, para
    que la auditoría de superficie lo vea (scripts/audit_rbac.py lee el árbol
    de deps)."""
    if not pnl_ajustes_sql.puede_escribir(actor):
        raise HTTPException(403, "sin permiso de escritura en ajustes de PnL")
    return actor


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
    está fuera del scope de grupos del usuario.

    `base="live_t1"`: la posición sale de `portafolio.tenencia_live` (t1 = con lo
    concertado HOY adentro) en vez de la foto conciliada de ayer. **Es el ÚNICO
    endpoint que lo pide** — la vista VALUACIONES, el asistente de IA y el cron de
    `pnl_totales_cache` comparten el mismo motor y siguen con la foto.

    Efecto colateral conocido: `/pnl-todas` lee `valuaciones.pnl_totales_cache`
    (la llena un cron con la foto), así que el LISTADO de cuentas y el DETALLE de
    una cuenta pueden mostrar totales distintos. No es un bug: son dos fuentes con
    distinta fecha base, y el detalle es el fresco.

    Si el daemon `tenencia_live` no corrió (fin de semana, caído), `_deps_sql` cae
    solo a la foto — la vista nunca queda vacía.
    """
    return pnl_sql.pnl_por_cuenta_sql(id_cuenta=id_cuenta, base="live_t1")


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


# ── Ajustes manuales de PnL (splits / eventos corporativos) ──────────────────
# Un split de CEDEAR (ej. YPF 10:1) no genera boleto → el cost-basis del motor
# queda desfasado y el PnL se rompe. Los ajustes viven en operaciones.pnl_ajustes
# y el motor los mergea al stream de boletos (ver pnl_ajustes_sql). Lectura con
# el módulo `portfolios` (gate del include); escritura SOLO admin + audit.

class _AjustePayload(BaseModel):
    tipo: str = Field(..., description="split | cantidad")
    ticker: str = Field(..., min_length=1, max_length=80)
    fecha: str = Field(..., description="YYYY-MM-DD — se aplica antes de los boletos del día")
    id_cuenta: str | None = Field(None, max_length=40, description="vacío = todas las cuentas")
    factor: float | None = Field(None, description="split: qty ×= factor (10 = 10:1, 0.1 = reverse)")
    cantidad: float | None = Field(None, description="cantidad: delta con signo")
    costo: float | None = Field(None, description="cantidad>0: costo asociado (opcional)")
    moneda: str = Field("ARS", max_length=8)
    nota: str | None = Field(None, max_length=300)
    activo: bool = True


@router.get("/pnl-ajustes")
def pnl_ajustes_listar(actor: str = Depends(get_user_email)) -> dict:
    """Todos los ajustes (activos e inactivos) + `puede_escribir` para que el
    front muestre u oculte el ABM. El PnL por cuenta se recalcula en vivo en
    cada request → un ajuste impacta al instante; la tab TOTALES lee el cache
    del cron (hasta 30' de retardo en rueda)."""
    return pnl_ajustes_sql.listar(email=actor)


@router.get("/pnl-ajustes/candidatos")
def pnl_ajustes_candidatos(actor: str = Depends(get_user_email)) -> dict:
    """Desfases detectados entre boletos y tenencia (completeness=parcial en el
    cache de TOTALES): los candidatos naturales a un ajuste. Si todas las cuentas
    de un ticker comparten el mismo ratio qty_aum/qty_calc, eso ES un evento
    corporativo y el ratio sugiere el factor. Admin ve todo; un operador solo
    los desfases de SUS cuentas."""
    return pnl_ajustes_sql.candidatos_desfase(email=actor)


@router.post("/pnl-ajustes", dependencies=[Depends(require_escritura_ajustes)])
def pnl_ajustes_crear(
    req: _AjustePayload = Body(...), actor: str = Depends(get_user_email),
) -> dict:
    try:
        return pnl_ajustes_sql.crear(req.model_dump(), actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e


@router.put("/pnl-ajustes/{ajuste_id}", dependencies=[Depends(require_escritura_ajustes)])
def pnl_ajustes_editar(
    ajuste_id: int, req: dict = Body(...), actor: str = Depends(get_user_email),
) -> dict:
    """Edición parcial: solo los campos presentes en el body pisan lo persistido
    (incluye `activo` para apagar/prender el ajuste sin borrarlo)."""
    try:
        return pnl_ajustes_sql.editar(ajuste_id, req, actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e


@router.delete("/pnl-ajustes/{ajuste_id}", dependencies=[Depends(require_escritura_ajustes)])
def pnl_ajustes_borrar(ajuste_id: int, actor: str = Depends(get_user_email)) -> dict:
    """Borrado real (el before queda en pnl_ajustes_audit). Para desactivar sin
    perder la carga usar PUT con activo=false."""
    try:
        return pnl_ajustes_sql.borrar(ajuste_id, actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e


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
