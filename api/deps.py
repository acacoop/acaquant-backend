"""Dependencias de FastAPI (auth) + re-export de helpers de DB.

La capa de servicios (`api/services/*`) importa los helpers de DB desde
`api/db.py` directo para no depender de fastapi. Los routers pueden seguir
importándolos desde acá por compat.
"""
import secrets

from fastapi import Header, HTTPException

from api.db import (  # noqa: F401 — reexport para compat
    get_db_cuentas,
    get_db_opciones,
    get_db_operaciones,
    get_db_portfolio,
    get_db_titulos,
    get_db_trading,
    get_db_valuaciones,
)
from config import API_KEY


def verify_api_key(authorization: str | None = Header(default=None)) -> None:
    """Valida el header Authorization: Bearer <API_KEY>.

    Si API_KEY no está configurada en .env, deja pasar todo (modo dev) —
    el header es opcional en ese caso. Si API_KEY está seteada, el header
    es obligatorio y debe matchear.

    Nota (EXT-AUTH1): en prod este "deja pasar todo" no se alcanza — el
    boot aborta si `ENV=prod` y falta `API_KEY` (ver
    `api.main._validar_postura_auth`). El fail-open queda solo para dev.
    """
    if not API_KEY:
        return
    if authorization is None or not secrets.compare_digest(
        authorization, f"Bearer {API_KEY}"
    ):
        raise HTTPException(status_code=401, detail="API key inválida")
