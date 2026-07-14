"""Hashing de passwords + emisión/validación de JWT para partner_api.

Password: PBKDF2-HMAC-SHA256 (stdlib `hashlib`, sin dependencias extra).
El hash se guarda como "salt_hex$hash_hex" en `ACAPortfolio.ApiUsers`. El
password en texto plano NO se persiste en ningún lado.

Token: JWT HS256 firmado con `PARTNER_JWT_SECRET`, de vida corta.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from datetime import UTC, datetime, timedelta

import jwt

from partner_api.settings import PARTNER_JWT_SECRET, PARTNER_TOKEN_TTL_MIN

_PBKDF2_ITER = 600_000
_JWT_ALG = "HS256"

# Identidad del emisor/destinatario del token. Acota el token a ESTE servicio:
# un JWT firmado con otro secreto/servicio (o un token de otra app que llegue a
# compartir el secreto) no pasa la validación aunque la firma dé.
_JWT_ISS = "acaquant-partner-api"
_JWT_AUD = "partner"

# Compatibilidad: los tokens EN VUELO del proveedor fueron emitidos SIN iss/aud.
# Con validación tolerante los aceptamos (si el claim no viene, no se exige; si
# viene, tiene que matchear) → nadie se come un 401 en el deploy. Poner esto en
# True cuando ya no queden tokens viejos vivos (basta con que pase el TTL:
# PARTNER_TOKEN_TTL_MIN, default 60') para exigir los claims siempre.
_JWT_CLAIMS_OBLIGATORIOS = False


# ── Passwords ────────────────────────────────────────────────────────────


def hash_password(password: str) -> str:
    """Devuelve "salt_hex$hash_hex" para guardar en la DB."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITER)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Compara `password` contra el "salt$hash" guardado. Timing-safe."""
    try:
        salt_hex, hash_hex = (stored or "").split("$", 1)
        salt = bytes.fromhex(salt_hex)
    except (ValueError, AttributeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITER)
    return hmac.compare_digest(dk.hex(), hash_hex)


# ── JWT ──────────────────────────────────────────────────────────────────


def crear_token(username: str) -> tuple[str, int]:
    """Emite un JWT para `username`. Devuelve (token, ttl_segundos)."""
    if not PARTNER_JWT_SECRET:
        raise RuntimeError("Falta PARTNER_JWT_SECRET en el entorno")
    now = datetime.now(UTC)
    exp = now + timedelta(minutes=PARTNER_TOKEN_TTL_MIN)
    payload = {
        "sub": username,
        "iat": now,
        "exp": exp,
        "iss": _JWT_ISS,
        "aud": _JWT_AUD,
    }
    token = jwt.encode(payload, PARTNER_JWT_SECRET, algorithm=_JWT_ALG)
    return token, PARTNER_TOKEN_TTL_MIN * 60


def _claim_ok(claims: dict, nombre: str, esperado: str) -> bool:
    """Valida `iss`/`aud` de forma TOLERANTE (ver `_JWT_CLAIMS_OBLIGATORIOS`).

    PyJWT no sirve para esto: si le pasás `audience=`/`issuer=` y el token no
    trae el claim, revienta con MissingRequiredClaim; y si NO se los pasás pero
    el token SÍ trae `aud`, revienta con InvalidAudience. Por eso se valida a
    mano con `verify_aud` desactivado.
    """
    val = claims.get(nombre)
    if val is None:
        return not _JWT_CLAIMS_OBLIGATORIOS
    if isinstance(val, list):          # `aud` puede ser lista según el RFC
        return esperado in val
    return val == esperado


def validar_token(token: str) -> str | None:
    """Devuelve el `username` del token si es válido y no venció; si no, None."""
    if not PARTNER_JWT_SECRET:
        return None
    try:
        claims = jwt.decode(
            token,
            PARTNER_JWT_SECRET,
            algorithms=[_JWT_ALG],
            options={"verify_aud": False},
        )
    except jwt.PyJWTError:
        return None
    if not _claim_ok(claims, "iss", _JWT_ISS) or not _claim_ok(claims, "aud", _JWT_AUD):
        return None
    sub = claims.get("sub")
    return sub if isinstance(sub, str) and sub else None
