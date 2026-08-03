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

import hashlib

from slowapi import Limiter
from starlette.requests import Request


def _key_by_user(request: Request) -> str:
    """Clave de rate limit: la identidad del usuario, lo más estable posible.

    Preferencia de fuente:
    1. `x-acaquant-user-email` — el email real del usuario cuando el request
       entra por el SSR del frontend (que pega con service token). Va PRIMERO:
       sin él, TODOS los usuarios de la mesa compartían un único bucket (el del
       service token), con lo cual uno solo podía agotar la cuota de todos y
       el límite por usuario no existía.
    2. `cf-access-authenticated-user-email` — user JWT con login directo.
    3. `cf-access-jwt-assertion` — sin email a mano, se usa un hash del JWT
       como proxy de sesión.
    4. IP (último recurso; con CF Tunnel casi siempre es loopback).

    LIMITACIÓN CONOCIDA: los headers 1 y 2 NO están validados criptográficamente
    acá (la validación real vive en `api/auth.py::get_user_email`, que corre
    después, en la dependency del endpoint). Quien pueda forjar headers puede
    rotarlos para estrenar bucket. No es la defensa contra un atacante con ese
    nivel de acceso — es reparto de cuota entre usuarios legítimos y freno a
    los accidentes (un loop en el frontend, un job desbocado). El gate de
    identidad es CF Access, no esto.
    """
    email = (request.headers.get("x-acaquant-user-email")
             or request.headers.get("cf-access-authenticated-user-email"))
    if email:
        return email.lower().strip()
    jwt = request.headers.get("cf-access-jwt-assertion")
    if jwt:
        # Hash en vez de los últimos 16 chars crudos: el sufijo de un JWT es
        # parte de la firma y no tiene por qué ser único entre sesiones.
        return "jwt:" + hashlib.sha256(jwt.encode("utf-8", "ignore")).hexdigest()[:32]
    return f"ip:{request.client.host if request.client else 'unknown'}"


# Techo global por identidad. Antes era `default_limits=[]`: sólo 4 endpoints
# de ~360 tenían límite, así que un cliente en loop sobre cualquier agregación
# cara (PnL, /historico/trades, tablero comercial) podía saturar el pool de
# Postgres que comparte toda la mesa.
#
# El número es DELIBERADAMENTE alto: una vista del frontend dispara decenas de
# requests al pintar, y este techo NO debe cortar uso normal — es un freno para
# el runaway, no una cuota de producto. Los endpoints que necesitan algo más
# estricto siguen declarando su propio `@limiter.limit(...)`, que pisa a este.
# Si aparecen 429 en uso legítimo, subirlo es la respuesta correcta.
_DEFAULT_LIMITS = ["600/minute", "20000/hour"]

# Instancia única compartida. Se monta en api/main.py via app.state.limiter.
limiter = Limiter(key_func=_key_by_user, default_limits=_DEFAULT_LIMITS)
