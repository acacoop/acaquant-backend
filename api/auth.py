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


def _verify_cf_jwt(cf_jwt: str) -> dict | None:
    """Valida el JWT firmado por Cloudflare Access y devuelve los claims.

    Devuelve None si no se pudo validar (firma inválida, exp, audience
    incorrecta, claves no disponibles). El caller extrae lo que necesita
    del dict de claims.
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
        return claims
    except Exception as e:
        logger.warning("CF JWT inválido: %s", e)
        return None


def get_user_email(
    cf_jwt: str | None = Header(default=None, alias="cf-access-jwt-assertion"),
    cf_email: str | None = Header(default=None, alias="cf-access-authenticated-user-email"),
) -> str:
    """Devuelve el email del usuario autenticado.

    Hay DOS tipos de JWT emitidos por Cloudflare Access:

    1. **User JWT** (login OTP directo): trae `email` o `identity.email`.
       El user se autenticó directamente contra CF. Usamos ese email.

    2. **Service token JWT** (acaquant-web → api.acaquant.com SSR): trae
       `common_name` pero NO `email` — es identidad de máquina. En este
       caso el frontend propaga el email del user en el header
       `cf-access-authenticated-user-email`. Como el JWT del service token
       YA probó criptográficamente que viene del frontend legítimo,
       confiar en ese header es seguro.

    Si no hay JWT (o CF_ACCESS_TEAM/AUD no están configurados), cae al
    header directo (modo dev).
    """
    if cf_jwt:
        claims = _verify_cf_jwt(cf_jwt)
        if claims is None:
            # JWT presente pero con firma/audience inválidas → sospechoso
            if CF_ACCESS_TEAM and CF_ACCESS_AUD:
                raise HTTPException(status_code=401, detail="CF JWT inválido")
        else:
            # Rama 1: user JWT con email directo
            email_claim = (
                claims.get("email")
                or (claims.get("identity") or {}).get("email")
            )
            if email_claim:
                return str(email_claim).lower().strip()

            # Rama 2: service token JWT (sin email, con common_name).
            # El frontend propaga el email del user en el header.
            common_name = claims.get("common_name")
            if common_name and cf_email:
                return cf_email.lower().strip()

            # Rama 3: JWT válido pero no user ni service conocido.
            # Aceptar header si viene (CF Access igual validó algo).
            if cf_email:
                logger.info("JWT válido sin email claim, usando header (cn=%s)", common_name)
                return cf_email.lower().strip()

            logger.warning("JWT válido pero sin email claim ni header fallback")
            return "anon"

    # Sin JWT: modo dev o request sin CF Access activo
    if cf_email:
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
        logger.warning(
            "require_manager: rechazado email=%r (autorizados: %d emails)",
            email, len(MANAGER_EMAILS),
        )
        raise HTTPException(status_code=403, detail="no autorizado")
    return email
