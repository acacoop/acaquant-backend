"""Hashing de passwords + emisión/validación de JWT para partner_api.

Password: PBKDF2-HMAC-SHA256 (stdlib `hashlib`, sin dependencias extra).
El hash se guarda como "salt_hex$hash_hex" en `Partner.ApiUsers`. El
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
    payload = {"sub": username, "iat": now, "exp": exp}
    token = jwt.encode(payload, PARTNER_JWT_SECRET, algorithm=_JWT_ALG)
    return token, PARTNER_TOKEN_TTL_MIN * 60


def validar_token(token: str) -> str | None:
    """Devuelve el `username` del token si es válido y no venció; si no, None."""
    if not PARTNER_JWT_SECRET:
        return None
    try:
        claims = jwt.decode(token, PARTNER_JWT_SECRET, algorithms=[_JWT_ALG])
    except jwt.PyJWTError:
        return None
    sub = claims.get("sub")
    return sub if isinstance(sub, str) and sub else None
