"""Autenticación de identidad — validación JWT de Cloudflare Access.

Reemplaza la lectura naïve del header `cf-access-authenticated-user-email`
(spoofable si alguien bypassa CF Access o pega con bearer válido localmente)
por validación criptográfica del JWT que emite Cloudflare Zero Trust.

Fail-open controlado: si CF_ACCESS_TEAM o CF_ACCESS_AUD no están
configurados en `.env`, el módulo loggea warning y cae al header sin
validar. Esto preserva el comportamiento actual en dev.

Uso:
    @router.post("")
    def chat(email: str = Depends(get_user_email), ...):
        # email viene del JWT validado, o del header si JWT no disponible
        ...

    # Gate de admin (MANAGER_EMAILS)
    app.include_router(manager.router, dependencies=[Depends(require_manager)])
"""
from __future__ import annotations

import logging
from functools import lru_cache

from fastapi import Depends, Header, HTTPException

from config import CF_ACCESS_AUD, CF_ACCESS_TEAM, MANAGER_EMAILS

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _jwks_client():
    """Cliente JWKS de Cloudflare Access (cachea las claves públicas).

    Se instancia solo si CF_ACCESS_TEAM está configurado. Lazy import de
    PyJWT para no forzar la dep en dev si no se usa.
    """
    if not CF_ACCESS_TEAM:
        return None
    try:
        import jwt  # PyJWT
    except ImportError:
        logger.warning("PyJWT no instalado — JWT de CF Access no se valida")
        return None
    url = f"https://{CF_ACCESS_TEAM}.cloudflareaccess.com/cdn-cgi/access/certs"
    return jwt.PyJWKClient(url)


def _verify_cf_jwt(cf_jwt: str) -> str | None:
    """Valida el JWT firmado por Cloudflare Access y devuelve el email claim.

    Devuelve None si no se pudo validar (JWT inválido, exp, audience
    incorrecta, claves no disponibles). El caller decide qué hacer.
    """
    if not (CF_ACCESS_TEAM and CF_ACCESS_AUD):
        return None
    client = _jwks_client()
    if client is None:
        return None
    try:
        import jwt
        signing_key = client.get_signing_key_from_jwt(cf_jwt).key
        claims = jwt.decode(
            cf_jwt,
            signing_key,
            audience=CF_ACCESS_AUD,
            algorithms=["RS256"],
        )
        email = claims.get("email") or claims.get("identity", {}).get("email")
        if not email:
            logger.warning("CF JWT válido pero sin email claim")
            return None
        return str(email).lower().strip()
    except Exception as e:
        logger.warning("CF JWT inválido: %s", e)
        return None


def get_user_email(
    cf_jwt: str | None = Header(default=None, alias="cf-access-jwt-assertion"),
    cf_email: str | None = Header(default=None, alias="cf-access-authenticated-user-email"),
) -> str:
    """Devuelve el email del usuario autenticado.

    Preferencia: JWT validado > header de email (fallback dev).
    Si no hay ninguno, devuelve "anon".

    En producción (CF_ACCESS_TEAM + CF_ACCESS_AUD seteados), siempre se
    valida el JWT. En dev, si no están configurados, se acepta el header
    con warning visible en los logs de cada request.
    """
    if cf_jwt:
        email = _verify_cf_jwt(cf_jwt)
        if email:
            return email
        # JWT presente pero no validó — es sospechoso. Log y 401.
        if CF_ACCESS_TEAM and CF_ACCESS_AUD:
            raise HTTPException(status_code=401, detail="CF JWT inválido")

    # Fallback al header spoofable — solo si JWT no está configurado
    if cf_email:
        if not (CF_ACCESS_TEAM and CF_ACCESS_AUD):
            # En dev dejamos pasar. En prod con JWT configurado ya
            # habría salido por la rama de arriba.
            return cf_email.lower().strip()

    return "anon"


def require_manager(email: str = Depends(get_user_email)) -> str:
    """Exige que el usuario esté en MANAGER_EMAILS. 403 si no.

    Si MANAGER_EMAILS está vacío en `.env`, deja pasar todo (modo dev) —
    igual que el proxy.ts del frontend acaquant-web.
    """
    if not MANAGER_EMAILS:
        return email  # dev: sin restricción
    if email not in MANAGER_EMAILS:
        raise HTTPException(status_code=403, detail="no autorizado")
    return email
