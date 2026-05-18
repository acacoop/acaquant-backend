"""Rate limiter del partner_api — slowapi, keyeado por IP del cliente.

Detrás de Cloudflare + nginx la IP real viene en `CF-Connecting-IP`; si
no, se cae a la IP de la conexión. Limita tanto la fuerza bruta sobre
`/v1/token` como el martilleo de los endpoints de datos.
"""
from __future__ import annotations

from slowapi import Limiter
from starlette.requests import Request


def client_ip(request: Request) -> str:
    """IP real del cliente — `CF-Connecting-IP` cuando viene por Cloudflare."""
    return (
        request.headers.get("cf-connecting-ip")
        or (request.client.host if request.client else "unknown")
    )


limiter = Limiter(key_func=client_ip, default_limits=["120/hour"])
