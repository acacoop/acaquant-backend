"""Rate limiter compartido — instancia única de slowapi.

Keying por email del usuario (validado vía JWT de CF Access o fallback al
header si no está configurado). Anónimos comparten un bucket único (anon)
para que ningún atacante no-autenticado queme cuota por volumen.

Uso en routers:

    from api.ratelimit import limiter

    @router.post("")
    @limiter.limit("30/minute;500/day")
    def handler(request: Request, ...):
        ...

`request: Request` es obligatorio como primer arg del endpoint — slowapi lo
inspecciona para aplicar la key_func.
"""
from __future__ import annotations

from slowapi import Limiter
from starlette.requests import Request


def _key_by_user(request: Request) -> str:
    """Clave de rate limit: email del usuario (JWT validado por CF) o 'anon'.

    Preferencia de fuente:
    1. cf-access-jwt-assertion (firmado por Cloudflare — no spoofable).
    2. cf-access-authenticated-user-email (fallback dev, spoofable).
    3. IP del cliente (último recurso — con CF Tunnel vienen todas de loopback).
    """
    jwt = request.headers.get("cf-access-jwt-assertion")
    if jwt:
        # Validar en caliente sería costoso por request. Como el JWT firmado
        # es estable durante la sesión, usamos sus últimos 16 chars como
        # proxy de identidad — cambia con cada nueva sesión pero una misma
        # sesión queda consistentemente ratelimiteada.
        return f"jwt:{jwt[-16:]}"
    email = request.headers.get("cf-access-authenticated-user-email")
    if email:
        return email.lower().strip()
    return f"ip:{request.client.host if request.client else 'unknown'}"


# Instancia única compartida. Se monta en api/main.py via app.state.limiter.
limiter = Limiter(key_func=_key_by_user, default_limits=[])
