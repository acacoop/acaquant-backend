"""Auth del partner_api — login usuario/password → JWT, y el guard de los
endpoints de datos.

Flujo: el proveedor hace `POST /v1/token` con usuario+password y recibe un
JWT de vida corta. Después manda `Authorization: Bearer <jwt>` en cada
request de datos.

Los usuarios viven en `Partner.ApiUsers`:
  {username, password_hash, enabled, created_at}
y se crean con `scripts/partner_user.py` (corre con el Mongo rw de la mesa
— este servicio es read-only y NO puede crear ni modificar usuarios).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm

from partner_api.db import get_db
from partner_api.ratelimit import client_ip, limiter
from partner_api.security import crear_token, hash_password, validar_token, verify_password

logger = logging.getLogger("partner_api.auth")

router = APIRouter(prefix="/v1", tags=["auth"])

_oauth2 = OAuth2PasswordBearer(tokenUrl="/v1/token")

# Hash dummy — se verifica contra esto cuando el usuario no existe, para
# que el tiempo de respuesta no revele si un usuario es válido o no.
_DUMMY_HASH = hash_password("dummy-no-user")


def _buscar_usuario(username: str) -> dict | None:
    return get_db()["ApiUsers"].find_one({"username": username})


@router.post("/token")
@limiter.limit("10/minute;100/hour")
def token(request: Request, form: OAuth2PasswordRequestForm = Depends()) -> dict:
    """Login del proveedor: usuario+password → JWT de vida corta.

    Rate-limit agresivo (10/min) para frenar fuerza bruta de password.
    """
    user = _buscar_usuario(form.username.strip())
    # Verificamos siempre (contra hash real o dummy) — mismo costo de tiempo
    # exista o no el usuario.
    stored = user["password_hash"] if user else _DUMMY_HASH
    ok = verify_password(form.password, stored)

    if not user or not ok or not user.get("enabled", False):
        logger.warning(
            "login fallido: user=%r ip=%s",
            form.username[:50], client_ip(request),
        )
        raise HTTPException(status_code=401, detail="usuario o password inválidos")

    access_token, ttl = crear_token(user["username"])
    logger.info("login OK: user=%s ip=%s", user["username"], client_ip(request))
    return {"access_token": access_token, "token_type": "bearer", "expires_in": ttl}


def usuario_actual(token: str = Depends(_oauth2)) -> str:
    """Dependency: valida el Bearer token y devuelve el username.

    Re-chequea contra la DB que el usuario siga existiendo y habilitado —
    así, deshabilitar un proveedor en `Partner.ApiUsers` lo deja afuera al
    instante, sin esperar a que venza su token.
    """
    username = validar_token(token)
    if not username:
        raise HTTPException(
            status_code=401,
            detail="token inválido o expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = _buscar_usuario(username)
    if not user or not user.get("enabled", False):
        raise HTTPException(status_code=401, detail="usuario deshabilitado")
    return username
