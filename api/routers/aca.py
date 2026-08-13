"""Router ACA — /api/aca (vista /aca, RESUMEN EJECUTIVO de la cartera propia).

LECTURA: módulo `aca` (rol `empleado_aca` + admin) ∪ escritores — se monta en
api/main.py con `require_lectura_aca`. ESCRITURA: allowlist de Mesa de Dinero
(`operaciones.mesa_dinero_escritores`) + admin, porque la mesa es la que maneja
la cuenta. Las dos son default-deny y se enforcean server-side.

Thin HTTP plumbing: toda la lógica (y TODA fórmula derivada) vive en
api/services/aca.py — el front no recalcula nada. Doc: docs/ACA.md.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services import aca as _svc

router = APIRouter(prefix="/api/aca", tags=["ACA"])


def require_lectura_aca(actor: str = Depends(get_user_email)) -> str:
    """Gate de ACCESO a la vista. Se monta a nivel router (api/main.py) → cubre
    TODOS los endpoints, incluidos los que se agreguen mañana."""
    if not _svc.puede_ver(email=actor):
        raise HTTPException(403, "sin acceso a la vista ACA")
    return actor


def require_escritura_aca(actor: str = Depends(get_user_email)) -> str:
    """Dependency (no chequeo dentro del handler) para que la auditoría de
    superficie lo vea: scripts/audit_rbac.py lee el árbol de deps."""
    if not _svc.puede_escribir(actor):
        raise HTTPException(403, "sin permiso de escritura en ACA")
    return actor


def _ok(fn, *args, **kwargs):
    """ValueError del service → 400 con el mensaje; PermissionError → 403.

    El service valida (y explica) porque es él quien conoce las reglas; el
    router solo traduce a HTTP.
    """
    try:
        return fn(*args, **kwargs)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ── Lectura ──────────────────────────────────────────────────────────────────

@router.get("/vista")
def vista(periodo: str | None = Query(None, description="'YYYY-MM'; default = el último cargado"),
          actor: str = Depends(get_user_email)) -> dict:
    """TODA la pantalla en un request (resumen + detalle + métricas + gráficos)."""
    return _ok(_svc.vista, periodo=periodo, email=actor)


@router.get("/periodos")
def periodos() -> dict:
    return _svc.listar_periodos()


@router.get("/resumen")
def resumen(periodo: str = Query(..., description="'YYYY-MM'")) -> dict:
    return _ok(_svc.resumen, periodo)


@router.get("/detalle")
def detalle(periodo: str = Query(..., description="'YYYY-MM'")) -> dict:
    return _ok(_svc.detalle, periodo)


@router.get("/metricas")
def metricas(periodo: str = Query(..., description="'YYYY-MM'")) -> dict:
    return _ok(_svc.metricas, periodo)


@router.get("/historico")
def historico(desde: str | None = Query(None), hasta: str | None = Query(None)) -> dict:
    return _ok(_svc.historico, desde=desde, hasta=hasta)


@router.get("/graficos")
def graficos(desde: str | None = Query(None), hasta: str | None = Query(None)) -> dict:
    return _ok(_svc.graficos, desde=desde, hasta=hasta)


@router.get("/titulos")
def titulos(q: str = Query("", description="Substring sobre unidad/ticker/emisor"),
            cartera: str | None = Query(None),
            limite: int = Query(40, ge=1, le=200)) -> dict:
    """Buscador sobre `portafolio.assets` para agregar una fila al detalle."""
    return _svc.buscar_titulos(q=q, cartera=cartera, limite=limite)


@router.get("/precios-sugeridos")
def precios_sugeridos(periodo: str = Query(..., description="'YYYY-MM'")) -> dict:
    """Último precio conocido de cada título del período — REFERENCIA, no se
    aplica solo: el precio del informe es el corte del mes y se tipea."""
    return _ok(_svc.precios_sugeridos, periodo)


@router.get("/catalogos")
def catalogos() -> dict:
    return _svc.catalogos()


# ── Escritura (allowlist de Mesa de Dinero + admin) ──────────────────────────

class _PeriodoPayload(BaseModel):
    periodo: str = Field(..., min_length=7, max_length=7, description="YYYY-MM")
    fecha_informe: str = Field(..., min_length=10, max_length=10, description="YYYY-MM-DD")
    mep: float | None = None
    a3500: float | None = None
    nota: str | None = Field(None, max_length=512)


@router.post("/periodos", dependencies=[Depends(require_escritura_aca)])
def guardar_periodo(req: _PeriodoPayload = Body(...),
                    actor: str = Depends(get_user_email)) -> dict:
    return _ok(_svc.guardar_periodo, req.model_dump(), actor=actor)


@router.delete("/periodos/{periodo}", dependencies=[Depends(require_escritura_aca)])
def borrar_periodo(periodo: str, actor: str = Depends(get_user_email)) -> dict:
    return _ok(_svc.borrar_periodo, periodo, actor=actor)


class _ClonarPayload(BaseModel):
    periodo: str = Field(..., min_length=7, max_length=7)
    origen: str | None = Field(None, min_length=7, max_length=7)


@router.post("/clonar", dependencies=[Depends(require_escritura_aca)])
def clonar(req: _ClonarPayload = Body(...), actor: str = Depends(get_user_email)) -> dict:
    """Copia la composición del mes anterior. NO copia precios a propósito."""
    return _ok(_svc.clonar_periodo, req.periodo, req.origen, actor=actor)


class _ActivoPayload(BaseModel):
    periodo: str = Field(..., min_length=7, max_length=7)
    unidad: str = Field(..., min_length=1, max_length=256)
    vn: float | None = None
    px: float | None = None
    monto: float | None = None          # override manual; None = derivar
    tasa: str | None = Field(None, max_length=64)
    obs: str | None = Field(None, max_length=128)
    orden: int = 0


@router.post("/activos", dependencies=[Depends(require_escritura_aca)])
def guardar_activo(req: _ActivoPayload = Body(...),
                   actor: str = Depends(get_user_email)) -> dict:
    return _ok(_svc.guardar_activo, req.model_dump(), actor=actor)


@router.delete("/activos", dependencies=[Depends(require_escritura_aca)])
def borrar_activo(periodo: str = Query(...), unidad: str = Query(...),
                  actor: str = Depends(get_user_email)) -> dict:
    return _ok(_svc.borrar_activo, periodo, unidad, actor=actor)


class _HistoricoPayload(BaseModel):
    periodo: str = Field(..., min_length=7, max_length=7)
    serie: str = Field(..., min_length=1, max_length=64)
    monto: float | None = None
    ingreso_retiro: float | None = None
    mensual: float | None = Field(None, description="FRACCIÓN: 0.0245 = 2,45%")


@router.post("/historico", dependencies=[Depends(require_escritura_aca)])
def guardar_historico(req: _HistoricoPayload = Body(...),
                      actor: str = Depends(get_user_email)) -> dict:
    return _ok(_svc.guardar_historico, req.model_dump(), actor=actor)


@router.delete("/historico", dependencies=[Depends(require_escritura_aca)])
def borrar_historico(periodo: str = Query(...), serie: str = Query(...),
                     actor: str = Depends(get_user_email)) -> dict:
    return _ok(_svc.borrar_historico, periodo, serie, actor=actor)
