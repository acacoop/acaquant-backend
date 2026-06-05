Proxy catch-all SOLO-GET hacia `/api/cotizaciones/*` del backend FastAPI. Reenvía sub-path y querystring inyectando credenciales de Cloudflare Access + API key, sin cache. Limitado a GET a propósito, para no exponer mutaciones (ej. PUT de tasa de opciones).

Conecta con: vistas de cotizaciones del front → este route → backend `api/routers/cotizaciones.py` en `api.acaquant.com`.
