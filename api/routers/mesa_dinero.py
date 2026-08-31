"""Router MESA DE DINERO — /api/mesa-dinero (vista NEGOCIO → /mesa-dinero).

LECTURA: allowlist per-usuario `operaciones.mesa_dinero_lectores` (∪ escritores)
+ admin — se monta en api/main.py con `require_lectura_mesa`. **NO** es el módulo
`operaciones` como el resto de NEGOCIO (cambio 2026-08-11): el acceso a esta
vista se decide por PERSONA, no por puesto. ESCRITURA: allowlist
`operaciones.mesa_dinero_escritores` + admin. Las dos se editan en Manager → MESA
y las dos son default-deny, con enforcement server-side.

LECTURA PARCIAL (2026-08-17): `operaciones.mesa_dinero_lectores_resultados` da
acceso SOLO a la tab RESULTADOS. El gate del router deja entrar a cualquier
alcance; los endpoints que alimentan las OTRAS tabs (`/ops` y `/resumen` → tab
OPERACIONES, `/retorno` → tab ACA VALORES RETORNO) llevan además
`require_vista_completa`. El corte está ACÁ y no en el front a propósito:
esconder una solapa no es un permiso — el dato seguiría estando a un request de
distancia.

CARGA DEL INFORME (2026-08-31): `POST /retorno/import` sube el Excel de ACA
VALORES desde la propia tab y es **admin-only** (`require_admin`) — antes la
única vía era correr un script en una consola. Reemplaza por PERÍODO igual que
la CLI porque es literalmente la misma función de service.

Thin HTTP plumbing: la lógica vive en api/services/mesa_dinero.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from api.auth import get_user_email, require_admin
from api.services import acavalores_retorno as _svc_ret
from api.services import mesa_dinero as _svc

router = APIRouter(prefix="/api/mesa-dinero", tags=["Mesa de Dinero"])


def require_lectura_mesa(actor: str = Depends(get_user_email)) -> str:
    """Gate de ACCESO a la vista: allowlist per-usuario + admin. Default-deny.

    Se monta a nivel router (api/main.py) → cubre TODOS los endpoints, incluidos
    los que se agreguen mañana. Reemplaza al gate de módulo `operaciones`."""
    if not _svc.puede_ver(email=actor):
        raise HTTPException(403, "sin acceso a Mesa de Dinero")
    return actor


def require_vista_completa(actor: str = Depends(get_user_email)) -> str:
    """Gate de las tabs que NO son RESULTADOS (operaciones del día, resumen
    diario + TC, ACA VALORES RETORNO). Deja pasar solo al alcance completo.

    Dependency (no chequeo dentro del handler) para que `scripts/audit_rbac.py`
    lo vea al recorrer el árbol de deps."""
    if not _svc.ve_todo(actor):
        raise HTTPException(403, "acceso limitado a la tab RESULTADOS de Mesa de Dinero")
    return actor


def require_escritura_mesa(actor: str = Depends(get_user_email)) -> str:
    """Dependency (no chequeo dentro del handler) para que la auditoría de
    superficie lo vea: scripts/audit_rbac.py lee el árbol de deps."""
    if not _svc.puede_escribir(actor):
        raise HTTPException(403, "sin permiso de escritura en Mesa de Dinero")
    return actor


# ── Lectura ──────────────────────────────────────────────────────────────────

@router.get("/ops", dependencies=[Depends(require_vista_completa)])
def listar_ops(
    desde: str | None = Query(None, description="YYYY-MM-DD"),
    hasta: str | None = Query(None, description="YYYY-MM-DD"),
    trader: str | None = Query(None, description="filtrar por trader exacto"),
) -> dict:
    return _svc.listar_ops(desde=desde, hasta=hasta, trader=trader)


@router.get("/resumen", dependencies=[Depends(require_vista_completa)])
def resumen(
    desde: str | None = Query(None, description="YYYY-MM-DD"),
    hasta: str | None = Query(None, description="YYYY-MM-DD"),
    trader: str | None = Query(None, description="filtrar por trader exacto"),
) -> dict:
    return _svc.resumen(desde=desde, hasta=hasta, trader=trader)


@router.get("/resultados")
def resultados(
    desde: str | None = Query(None, description="YYYY-MM-DD"),
    hasta: str | None = Query(None, description="YYYY-MM-DD"),
    trader: str | None = Query(None, description="filtrar por trader exacto"),
) -> dict:
    return _svc.resultados(desde=desde, hasta=hasta, trader=trader)


@router.get("/opciones")
def opciones(actor: str = Depends(get_user_email)) -> dict:
    return _svc.opciones(email=actor)


@router.get("/retorno", dependencies=[Depends(require_vista_completa)])
def retorno(periodo: str | None = Query(None, description="'YYYY-MM'; default = más reciente")) -> dict:
    """ACA VALORES RETORNO TOTAL — filas del período (el front agrega por CASH)."""
    return _svc_ret.panel(periodo=periodo)


@router.post("/retorno/import", dependencies=[Depends(require_admin)])
async def importar_retorno(
    archivo: UploadFile = File(..., description="informe 'OP Aca Valores FCI' (.xls)"),
    dry_run: bool = Query(False, description="preview: parsea y resume SIN escribir"),
    actor: str = Depends(get_user_email),
) -> dict:
    """Sube el informe Excel y reemplaza los períodos que trae. ADMIN-ONLY.

    Gate `require_admin` y NO `require_vista_completa`: es estrictamente más
    fuerte (admin siempre tiene alcance completo — ver mesa_dinero.alcance) y
    deja el permiso donde el user lo pidió, sin abrir una allowlist nueva que
    después haya que mantener. Sumar los dos gates no agregaría seguridad y sí
    rompería el test que congela cuáles rutas llevan `require_vista_completa`.

    Es la MISMA función que corre la CLI (`scripts/import_acavalores_retorno`),
    así que la idempotencia por período vale igual: importar agosto reemplaza
    agosto y no toca ningún otro mes.
    """
    contenido = await archivo.read()
    if not contenido:
        raise HTTPException(400, "El archivo llegó vacío.")
    if len(contenido) > _svc_ret.MAX_BYTES:
        raise HTTPException(
            413, f"El archivo pesa {len(contenido) / 1e6:.1f} MB; el tope es "
                 f"{_svc_ret.MAX_BYTES // (1024 * 1024)} MB.")
    try:
        return _svc_ret.importar(contenido, archivo=archivo.filename or "sin_nombre",
                                 actor=actor, dry_run=dry_run)
    except ValueError as e:      # no es el informe / sin períodos → error del usuario
        raise HTTPException(400, str(e)) from e


# ── Escritura (allowlist + admin) ────────────────────────────────────────────

class _OpPayload(BaseModel):
    fecha: str = Field(..., min_length=10, max_length=10, description="YYYY-MM-DD")
    trader: str = Field(..., min_length=1, max_length=128)
    activo: str | None = Field(None, max_length=128)
    vn_compra: float | None = None
    px_compra: float | None = None
    vn_venta: float | None = None
    px_venta: float | None = None
    # Solo para registros SIN patas (ej. "Pase OPS"); con patas se deriva y se ignora.
    resultado: float | None = None
    cliente: str | None = Field(None, max_length=256)
    observacion: str | None = Field(None, max_length=128)


@router.post("/ops", dependencies=[Depends(require_escritura_mesa)])
def crear_op(req: _OpPayload = Body(...), actor: str = Depends(get_user_email)) -> dict:
    try:
        return _svc.crear_op(req.model_dump(), actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.patch("/ops/{op_id}", dependencies=[Depends(require_escritura_mesa)])
def editar_op(op_id: int, req: _OpPayload = Body(...),
              actor: str = Depends(get_user_email)) -> dict:
    try:
        return _svc.editar_op(op_id, req.model_dump(), actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/ops/{op_id}", dependencies=[Depends(require_escritura_mesa)])
def borrar_op(op_id: int, actor: str = Depends(get_user_email)) -> dict:
    try:
        return _svc.borrar_op(op_id, actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class _TcPayload(BaseModel):
    fecha: str = Field(..., min_length=10, max_length=10, description="YYYY-MM-DD")
    tc: float = Field(..., gt=0)


@router.put("/tc", dependencies=[Depends(require_escritura_mesa)])
def set_tc(req: _TcPayload = Body(...), actor: str = Depends(get_user_email)) -> dict:
    try:
        return _svc.set_tc(req.fecha, req.tc, actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
