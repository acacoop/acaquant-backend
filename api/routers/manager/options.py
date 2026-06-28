"""GET/PUT /api/manager/options/expiries — config del engine de opciones (SQL-native)."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel

from api.routers.manager._common import _AR_TZ
from core import pg_mirror

router = APIRouter()


@router.get("/options/expiries")
def get_options_expiries():
    """Lee la config del engine desde SQL (mercado.options_metadata.config):
    vencimientos disponibles (publicados por el engine) y los activos (elegidos por
    el user en el Manager).

    Si `activos` está vacía → el engine hace auto-pick del próximo > hoy.
    """
    cfg = pg_mirror.read_native_doc("options_metadata", ["type"], ["config"])
    disponibles = cfg.get("expiries_disponibles", []) or []
    activos     = cfg.get("expiries", []) or []
    actualizado = cfg.get("expiries_updated_at")
    # En SQL el timestamp se guarda como ISO string (doc_iso); en el path Mongo
    # legacy podía venir como datetime. Aceptamos ambos.
    if isinstance(actualizado, str):
        try:
            actualizado = datetime.fromisoformat(actualizado)
        except ValueError:
            actualizado = None
    if isinstance(actualizado, datetime):
        actualizado = (actualizado if actualizado.tzinfo else actualizado.replace(tzinfo=UTC)) \
            .astimezone(_AR_TZ).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "disponibles": disponibles,
        "activos":     activos,
        "auto_pick":   not activos,
        "actualizado": actualizado,
        "mapa_size":   cfg.get("mapa_size"),
    }


class _ExpiriesPayload(BaseModel):
    expiries: list[str] | None = None  # None o [] → auto-pick


@router.put("/options/expiries")
def put_options_expiries(payload: _ExpiriesPayload):
    """Persiste los vencimientos elegidos en Opciones.Metadata. El engine los
    toma en el próximo chequeo periódico (~5 min) y refresca su mapa."""
    valor = [e for e in (payload.expiries or []) if isinstance(e, str) and len(e) == 8]
    # SQL-native: mergea SOLO `expiries`/`expiries_updated_by_ui` sobre config sin pisar
    # `tasa` ni `expiries_disponibles` (que escribe el motor). El motor lo lee en su
    # próximo chequeo periódico (~5 min) y refresca su mapa.
    pg_mirror.merge_jsonb_native(
        "options_metadata", ["type"], ["config"],
        {"expiries": valor, "expiries_updated_by_ui": datetime.now(UTC)},
    )
    return {"ok": True, "expiries": valor, "auto_pick": not valor}
