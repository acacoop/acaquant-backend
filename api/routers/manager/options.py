"""GET/PUT /api/manager/options/expiries — config del engine de opciones."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel

from api.routers.manager._common import _AR_TZ
from core.mongo import get_mongo_client, get_mongo_client_read

router = APIRouter()


@router.get("/options/expiries")
def get_options_expiries():
    """Lee Opciones.Metadata: vencimientos disponibles (publicados por el
    engine) y los activos (elegidos por el user en el Manager).

    Si `activos` está vacía → el engine hace auto-pick del próximo > hoy.
    """
    cfg = (
        get_mongo_client_read()["Opciones"]["Metadata"].find_one({"type": "config"}) or {}
    )
    disponibles = cfg.get("expiries_disponibles", []) or []
    activos     = cfg.get("expiries", []) or []
    actualizado = cfg.get("expiries_updated_at")
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
    get_mongo_client()["Opciones"]["Metadata"].update_one(
        {"type": "config"},
        {"$set": {"expiries": valor, "expiries_updated_by_ui": datetime.now(UTC)}},
        upsert=True,
    )
    return {"ok": True, "expiries": valor, "auto_pick": not valor}
