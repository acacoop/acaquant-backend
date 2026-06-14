"""Manager sub-router — import masivo de tenencia a Valuaciones.AuM (admin).

Tab `/manager → IMPORTAR TENENCIA`. Sube un Excel (parseado en el front) con la
tenencia correcta del sistema contable y pisa el AuM en esas fechas/cuentas.
Pensado para corregir los fines de mes que quedaron mal por el corrimiento de
fecha del job. Ver `api/services/import_tenencia.py`.

  POST /api/manager/import-tenencia  body {rows:[...], commit:false}
    commit=false → previsualiza (no escribe): conteos, cuentas/fechas, errores,
                   especies sin match en Assets.
    commit=true  → aplica: delete+insert por (fecha, cuenta). Idempotente.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, Field

from api.auth import get_user_email
from api.services import import_tenencia as svc
from api.services import import_tenencia_sql as svc_sql

router = APIRouter()


class _Row(BaseModel):
    fecha:     str
    id_cuenta: str | int
    cuenta:    str | None = None
    unidad:    str
    cantidad:  float | str | None = None
    precio:    float | str | None = None
    valuacion: float | str | None = None
    moneda:    str | None = None


class _ImportReq(BaseModel):
    rows:   list[_Row] = Field(..., min_length=1)
    commit: bool = False


@router.post("/import-tenencia")
def import_tenencia(req: _ImportReq = Body(...), actor: str = Depends(get_user_email)) -> dict:
    """Previsualiza (commit=false) o aplica (commit=true) el import de tenencia."""
    return svc.importar(
        rows=[r.model_dump() for r in req.rows], actor=actor, commit=req.commit)


# ── Import a SQL portafolio.tenencia — 2 modos (Manager → AUNESA → IMPORTAR) ──
class _ImportRows(BaseModel):
    rows:   list[dict] = Field(..., min_length=1)
    commit: bool = False


@router.post("/import-precios-sql")
def import_precios_sql(req: _ImportRows = Body(...),
                       actor: str = Depends(get_user_email)) -> dict:
    """MODO PRECIOS: Excel [unidad, precio, fecha] → actualiza `precio` en
    portafolio.tenencia por (fecha, unidad). NO recalcula valuación."""
    return svc_sql.importar_precios(rows=req.rows, commit=req.commit)


@router.post("/import-aum-sql")
def import_aum_sql(req: _ImportRows = Body(...),
                   actor: str = Depends(get_user_email)) -> dict:
    """MODO AUM: Excel [Cuenta, Unidad, Cantidad, Fecha, Precio, Valuación] → pisa
    portafolio.tenencia por (fecha, id_cuenta). Setea `aum` con _aum_filters."""
    return svc_sql.importar_aum(rows=req.rows, actor=actor, commit=req.commit)
