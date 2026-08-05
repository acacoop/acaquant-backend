"""Router SENEBIS — /api/back-office/senebis (vista BACK OFFICE → SENEBIS).

Órdenes que los TRADERS cargan para que el BACK OFFICE las procese afuera y
las marque 'completada'. Gate: módulo `back-office` (se monta en api/main.py
con _BACK_OFFICE — traders y back office lo tienen en la matriz).

CARGAR/EDITAR/BORRAR órdenes exige además la MISMA allowlist de escritura de
Mesa de Dinero (`mesa_dinero_escritores` + admin, Manager → MESA) —
enforcement server-side en cada write, default-deny. Marcar estado y el
catálogo de agentes quedan para todo el módulo (es el trabajo del back
office, que no necesariamente carga en la mesa).

GET /ops además marca presencia del caller — el polling de la lista es el
heartbeat; la respuesta trae `conectados` para que el equipo vea quién está
en la vista y no se pisen. Thin HTTP plumbing: la lógica vive en
api/services/senebis.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services import senebis as _svc

router = APIRouter(prefix="/api/back-office/senebis", tags=["Senebis"])


def _exigir_escritura(actor: str) -> None:
    if not _svc.puede_escribir(actor):
        raise HTTPException(
            403, "sin permiso para cargar órdenes SENEBIS (misma allowlist "
                 "que Mesa de Dinero — se gestiona en Manager → MESA)")


# ── Lectura ──────────────────────────────────────────────────────────────────

@router.get("/ops")
def listar_ops(
    desde: str | None = Query(None, description="YYYY-MM-DD (concertación)"),
    hasta: str | None = Query(None, description="YYYY-MM-DD (concertación)"),
    estado: str | None = Query(None, description="pendiente | completada"),
    especie: str | None = Query(None, description="filtro por especie (contiene)"),
    mae: str | None = Query(None, description="solo | sin (vacío = todas, MAE incluidas)"),
    actor: str = Depends(get_user_email),
) -> dict:
    """Lista de órdenes + `conectados` (presencia). Pollear esto mantiene vivo
    el heartbeat del caller."""
    try:
        return _svc.listar_ops(desde=desde, hasta=hasta, estado=estado,
                               especie=especie, mae=mae, email=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/presencia")
def presencia(actor: str = Depends(get_user_email)) -> dict:
    """Heartbeat explícito (opcional — GET /ops ya marca presencia)."""
    _svc.marcar_presencia(actor)
    return {"conectados": _svc.conectados()}


@router.get("/opciones")
def opciones(actor: str = Depends(get_user_email)) -> dict:
    """Opciones del form: catálogo de agentes externos + valores válidos."""
    return _svc.opciones(email=actor)


@router.get("/comitentes")
def comitentes(
    q: str = Query(..., min_length=1, description="número de cuenta o denominación (contiene)"),
    _actor: str = Depends(get_user_email),
) -> dict:
    """Autocomplete de cuentas comitentes (senebi interno): el trader busca por
    número O por nombre y el form completa el que falte."""
    return {"comitentes": _svc.buscar_comitentes(q=q)}


@router.get("/excel")
def excel_preview(
    desde: str | None = Query(None, description="YYYY-MM-DD (concertación)"),
    hasta: str | None = Query(None, description="YYYY-MM-DD (concertación)"),
    actor: str = Depends(get_user_email),
) -> dict:
    """Espejo en vivo del Excel destino (tab EXCEL QUANTEX): mismas filas y
    reglas que /export — SOLO pendientes no-MAE (lo completado ya se cargó
    en Quantex). Marca presencia del caller."""
    try:
        return _svc.excel_preview(desde=desde, hasta=hasta, email=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/export")
def export(
    desde: str | None = Query(None, description="YYYY-MM-DD (concertación)"),
    hasta: str | None = Query(None, description="YYYY-MM-DD (concertación)"),
    _actor: str = Depends(get_user_email),
) -> Response:
    """Descarga el .xlsx que se carga en el sistema destino (ID · OPERACION ·
    INSTRUMENTO · PLAZO · PRECIO · CANTIDAD · CONTRAPARTE · COMITENTE ·
    CARTERA PROPIA · MERCADO)."""
    try:
        contenido, nombre = _svc.export_xlsx(desde=desde, hasta=hasta)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:  # openpyxl no instalado en el venv
        raise HTTPException(501, str(e)) from e
    return Response(
        content=contenido,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


# ── Escritura ────────────────────────────────────────────────────────────────

class _OpPayload(BaseModel):
    operacion: str = Field(..., min_length=1, max_length=16, description="COMPRA | VENTA")
    # Default HOY (lo pone el service, timezone ART).
    concertacion: str | None = Field(None, min_length=10, max_length=10, description="YYYY-MM-DD")
    # plazo ↔ liquidacion se infieren entre sí server-side (CI = mismo día,
    # 24 = próximo hábil); mandar cualquiera de los dos alcanza.
    liquidacion: str | None = Field(None, min_length=10, max_length=10, description="YYYY-MM-DD")
    plazo: str | None = Field(None, max_length=8, description="CI | 24")
    especie: str = Field(..., min_length=1, max_length=64)
    vn: float | None = None
    px: float | None = Field(None, description="precio cada 100 VN")
    # Derivado server-side (vn × px / 100); mandarlo explícito lo pisa.
    monto: float | None = None
    cp: str | None = Field(None, max_length=32, description="cartera propia (default 255)")
    cc: str | None = Field(None, max_length=128, description="cuenta comitente (texto o número)")
    contraparte: str | None = Field(None, max_length=128)
    nro_contraparte: str | None = Field(None, max_length=64)
    mercado: str | None = Field(None, max_length=64, description="GARANTIZADO | NO GARANTIZADO | vacío")
    cargan_ellos: str | None = Field(None, max_length=256, description="observación libre")
    tipo: str | None = Field(None, max_length=256, description="observación libre")
    # Contraparte del senebi: interno (cliente ALyC → cc por número o denominación)
    # o externo (agente del catálogo → el número sale del catálogo al Excel).
    tipo_contraparte: str = Field("interno", description="interno | externo")
    agente: str | None = Field(None, max_length=128, description="nombre del agente (externo)")
    # MAE: se carga en el MAE (no en Quantex) → excluida del Excel/espejo,
    # tipo queda 'MAE' automático.
    es_mae: bool = False


@router.post("/ops")
def crear_op(req: _OpPayload = Body(...), actor: str = Depends(get_user_email)) -> dict:
    _exigir_escritura(actor)
    try:
        return _svc.crear_op(req.model_dump(), actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.patch("/ops/{op_id}")
def editar_op(op_id: int, req: _OpPayload = Body(...),
              actor: str = Depends(get_user_email)) -> dict:
    _exigir_escritura(actor)
    try:
        return _svc.editar_op(op_id, req.model_dump(), actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/ops/{op_id}")
def borrar_op(op_id: int, actor: str = Depends(get_user_email)) -> dict:
    _exigir_escritura(actor)
    try:
        return _svc.borrar_op(op_id, actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/ops/{op_id}/visto")
def limpiar_marcas(op_id: int, actor: str = Depends(get_user_email)) -> dict:
    """Baja las marcas de edición (el * y el amarillo) — el back office ya
    revisió el cambio en Quantex. No es escritura de la orden: lo puede hacer
    todo el módulo `back-office`, que es quien las revisa."""
    try:
        return _svc.limpiar_marcas(op_id, actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class _ProximoIdPayload(BaseModel):
    siguiente: int = Field(..., gt=0, description="próximo ID a asignar (último del Excel viejo + 1)")


@router.post("/proximo-id")
def set_proximo_id(req: _ProximoIdPayload = Body(...),
                   actor: str = Depends(get_user_email)) -> dict:
    """Alinea la secuencia de IDs con la numeración real (Excel viejo/Quantex).
    Solo escritores + admin; nunca retrocede por debajo del último ID cargado."""
    _exigir_escritura(actor)
    try:
        return _svc.set_proximo_id(req.siguiente, actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class _AgentePayload(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=128)
    numero: str = Field(..., min_length=1, max_length=32,
                        description="número de agente del sistema destino")


@router.put("/agentes")
def upsert_agente(req: _AgentePayload = Body(...),
                  actor: str = Depends(get_user_email)) -> dict:
    """Alta/edición de un agente externo (catálogo nombre → número)."""
    try:
        return _svc.upsert_agente(req.nombre, req.numero, actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/agentes/{nombre}")
def quitar_agente(nombre: str, actor: str = Depends(get_user_email)) -> dict:
    try:
        return _svc.quitar_agente(nombre, actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class _EstadoPayload(BaseModel):
    estado: str = Field(..., description="pendiente | completada")


@router.post("/ops/{op_id}/estado")
def set_estado(op_id: int, req: _EstadoPayload = Body(...),
               actor: str = Depends(get_user_email)) -> dict:
    """El back office marca la orden 'completada' (o la vuelve a 'pendiente')."""
    try:
        return _svc.set_estado(op_id, req.estado, actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
