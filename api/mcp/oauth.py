"""OAuth 2.1 provider para el MCP server — login delegado a Cloudflare Access.

Flujo:
  1. Claude Desktop hace DCR (POST /oauth/register) con sus redirect_uris.
  2. Abre browser en /oauth/authorize → CF Access desafía al user con email+OTP.
  3. Después que CF lo deja pasar, leemos el JWT de CF, sacamos el email, y
     emitimos un authorization code que redirige a Claude.
  4. Claude intercambia el code por un access_token via POST /oauth/token
     (con PKCE).
  5. Claude usa el access_token (un JWT firmado por nosotros) en cada request
     al MCP. El middleware lo valida.

Storage: Mongo db `MCP`, colecciones `OAuthClients` / `OAuthCodes` / `OAuthTokens`.
TTL automático en codes (10min) y tokens (1h).
"""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from api.auth import get_user_email
from config import MCP_JWT_SECRET, MCP_OAUTH_ISSUER
from core.mongo import get_mongo_client

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────

ACCESS_TOKEN_TTL_SECONDS = 3600       # 1 hora
AUTH_CODE_TTL_SECONDS    = 600        # 10 minutos
JWT_ALG                  = "HS256"
SUPPORTED_SCOPES         = ("mcp:read",)


# ─────────────────────────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────────────────────────


def _db():
    """Mongo db `MCP` (separada de Trading/Manager/Valuaciones)."""
    return get_mongo_client()["MCP"]


def _ensure_indexes() -> None:
    """Idempotente: TTL en codes y tokens, unique en client_id."""
    db = _db()
    db["OAuthCodes"].create_index("expires_at", expireAfterSeconds=0)
    db["OAuthTokens"].create_index("expires_at", expireAfterSeconds=0)
    db["OAuthClients"].create_index("client_id", unique=True)


def _save_client(client_id: str, redirect_uris: list[str], client_name: str) -> None:
    _db()["OAuthClients"].update_one(
        {"client_id": client_id},
        {"$set": {
            "client_id": client_id,
            "redirect_uris": redirect_uris,
            "client_name": client_name,
            "created_at": datetime.now(UTC),
        }},
        upsert=True,
    )


def _get_client(client_id: str) -> dict | None:
    return _db()["OAuthClients"].find_one({"client_id": client_id})


def _save_authorization_code(
    code: str, client_id: str, redirect_uri: str, scope: str, subject: str,
    code_challenge: str, code_challenge_method: str,
) -> None:
    _db()["OAuthCodes"].insert_one({
        "code": code,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "subject": subject,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "created_at": datetime.now(UTC),
        "expires_at": datetime.now(UTC) + timedelta(seconds=AUTH_CODE_TTL_SECONDS),
    })


def _consume_authorization_code(code: str) -> dict | None:
    """Retorna el doc Y lo elimina (single use). None si no existe o expiró."""
    return _db()["OAuthCodes"].find_one_and_delete({"code": code})


def _save_token(jti: str, subject: str, client_id: str, scope: str) -> None:
    _db()["OAuthTokens"].insert_one({
        "jti": jti,
        "subject": subject,
        "client_id": client_id,
        "scope": scope,
        "created_at": datetime.now(UTC),
        "expires_at": datetime.now(UTC) + timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS),
    })


def is_token_revoked(jti: str) -> bool:
    """Si el JWT no está en OAuthTokens (TTL barrió o nunca se emitió), tratamos
    como revocado. Esto da revocación gratis: borrás el doc y el token muere."""
    return _db()["OAuthTokens"].find_one({"jti": jti}) is None


# ─────────────────────────────────────────────────────────────────
# JWT helpers
# ─────────────────────────────────────────────────────────────────


def issue_access_token(subject: str, client_id: str, scope: str) -> tuple[str, int]:
    """Devuelve (access_token, expires_in_seconds)."""
    now = datetime.now(UTC)
    jti = secrets.token_urlsafe(16)
    payload = {
        "iss": MCP_OAUTH_ISSUER,
        "aud": MCP_OAUTH_ISSUER,
        "sub": subject,
        "client_id": client_id,
        "scope": scope,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS)).timestamp()),
        "jti": jti,
    }
    token = jwt.encode(payload, MCP_JWT_SECRET, algorithm=JWT_ALG)
    _save_token(jti=jti, subject=subject, client_id=client_id, scope=scope)
    return token, ACCESS_TOKEN_TTL_SECONDS


def verify_access_token(token: str) -> dict | None:
    """Decodifica y valida un JWT OAuth-issued. None si falla.
    Chequea firma, expiry, audience, y que el jti siga vivo (no revocado)."""
    if not MCP_JWT_SECRET:
        return None
    try:
        claims = jwt.decode(
            token, MCP_JWT_SECRET,
            algorithms=[JWT_ALG],
            audience=MCP_OAUTH_ISSUER,
            issuer=MCP_OAUTH_ISSUER,
        )
    except jwt.PyJWTError as e:
        logger.debug("JWT MCP inválido: %s", e)
        return None
    jti = claims.get("jti")
    if not jti or is_token_revoked(jti):
        logger.debug("JWT MCP revocado o sin jti")
        return None
    return claims


# ─────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────


router = APIRouter(tags=["MCP OAuth"])


class _RegisterRequest(BaseModel):
    """RFC 7591 — Dynamic Client Registration. Aceptamos el subset que envía
    Claude Desktop / claude.ai. Ignoramos el resto."""
    redirect_uris: list[str] = Field(..., min_length=1)
    client_name: str | None = None
    token_endpoint_auth_method: str | None = "none"


class _RegisterResponse(BaseModel):
    client_id: str
    client_id_issued_at: int
    redirect_uris: list[str]
    token_endpoint_auth_method: str = "none"
    grant_types: list[str] = ["authorization_code"]
    response_types: list[str] = ["code"]


@router.post("/oauth/register", response_model=_RegisterResponse)
async def register_client(body: _RegisterRequest):
    """RFC 7591: Dynamic Client Registration para clientes públicos (PKCE)."""
    _ensure_indexes()
    client_id = "mcp_" + secrets.token_urlsafe(16)
    name = body.client_name or "unnamed"
    _save_client(client_id=client_id, redirect_uris=body.redirect_uris, client_name=name)
    logger.info("DCR: nuevo client %s name=%r redirects=%s", client_id, name, body.redirect_uris)
    return _RegisterResponse(
        client_id=client_id,
        client_id_issued_at=int(datetime.now(UTC).timestamp()),
        redirect_uris=body.redirect_uris,
    )


@router.get("/oauth/authorize")
async def authorize(
    request: Request,
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    response_type: str = Query("code"),
    scope: str = Query("mcp:read"),
    state: str = Query(...),
    code_challenge: str = Query(...),
    code_challenge_method: str = Query("S256"),
):
    """User está acá DESPUÉS de pasar por CF Access. Leemos el email del JWT
    de CF y emitimos un authorization code."""
    if response_type != "code":
        raise HTTPException(400, "Solo se soporta response_type=code")
    if code_challenge_method != "S256":
        raise HTTPException(400, "Solo se soporta code_challenge_method=S256")

    client = _get_client(client_id)
    if not client:
        raise HTTPException(400, f"client_id desconocido: {client_id}")
    if redirect_uri not in client["redirect_uris"]:
        raise HTTPException(400, "redirect_uri no registrado para este client")

    # Identidad: viene del JWT de CF Access (header cf-access-jwt-assertion).
    # Si CF no validó al user, get_user_email devuelve "anon" (modo dev).
    cf_jwt = request.headers.get("cf-access-jwt-assertion")
    cf_email_header = request.headers.get("cf-access-authenticated-user-email")
    email = get_user_email(cf_jwt=cf_jwt, cf_email=cf_email_header)
    if email == "anon":
        raise HTTPException(401, "No se pudo identificar al usuario via CF Access")

    code = secrets.token_urlsafe(32)
    _save_authorization_code(
        code=code,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        subject=email,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
    )

    # Redirigimos al cliente (Claude Desktop / claude.ai) con code + state.
    qs = urlencode({"code": code, "state": state})
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(url=f"{redirect_uri}{sep}{qs}", status_code=302)


@router.post("/oauth/token")
async def token_endpoint(
    grant_type: str = Form(...),
    code: str = Form(...),
    redirect_uri: str = Form(...),
    client_id: str = Form(...),
    code_verifier: str = Form(...),
):
    """Intercambia authorization_code por access_token. PKCE obligatorio."""
    if grant_type != "authorization_code":
        return JSONResponse(
            {"error": "unsupported_grant_type"}, status_code=400,
        )

    auth_code_doc = _consume_authorization_code(code)
    if not auth_code_doc:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    # PyMongo devuelve datetimes naive (sin tz) por default; comparar contra
    # datetime.now(UTC) (aware) tira TypeError → 500. Normalizamos a UTC.
    expires_at = auth_code_doc["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < datetime.now(UTC):
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if auth_code_doc["client_id"] != client_id:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if auth_code_doc["redirect_uri"] != redirect_uri:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)

    # PKCE S256
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode("ascii")).digest(),
    ).rstrip(b"=").decode("ascii")
    if expected != auth_code_doc["code_challenge"]:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)

    access_token, ttl = issue_access_token(
        subject=auth_code_doc["subject"],
        client_id=client_id,
        scope=auth_code_doc["scope"],
    )
    logger.info(
        "OAuth: emitido access_token para %s client=%s",
        auth_code_doc["subject"], client_id,
    )
    return JSONResponse({
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": ttl,
        "scope": auth_code_doc["scope"],
    })
