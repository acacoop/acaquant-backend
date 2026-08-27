"""api/ext/ratelimit.py — cuota de la API externa, por CLIENTE.

Instancia propia, separada de `api/ratelimit.py`: aquella keyea por email de la
mesa, que acá no existe. La clave es la identidad de máquina más estable que
tenemos ANTES de autenticar (el rate limit corre antes que la dependency de
auth, así que no puede usar el `Cliente` ya resuelto).

Orden de preferencia:
 1. `CF-Access-Client-Id` — el service token de Cloudflare. En prod es confiable
    de verdad: CF lo valida en el borde y un request con uno inventado no llega.
 2. Hash del bearer — cuando todavía no hay service token (dev, curl local).
 3. IP — último recurso.
"""
from __future__ import annotations

import hashlib

from slowapi import Limiter
from starlette.requests import Request


def _key_por_cliente(request: Request) -> str:
    cf = request.headers.get("cf-access-client-id")
    if cf:
        return f"cf:{cf.strip().lower()}"
    auth = request.headers.get("authorization")
    if auth:
        return "tok:" + hashlib.sha256(auth.encode("utf-8", "ignore")).hexdigest()[:32]
    return f"ip:{request.client.host if request.client else 'unknown'}"


# Techo por cliente. Generoso para una integración normal (una corrida
# incremental son pocas decenas de requests) y freno para el loop desbocado.
# Un endpoint puede declarar el suyo propio y pisa a éste.
_LIMITES = ["120/minute", "5000/hour"]

limiter = Limiter(key_func=_key_por_cliente, default_limits=_LIMITES)
