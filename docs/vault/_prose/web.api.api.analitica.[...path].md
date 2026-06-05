Proxy catch-all (GET y POST) del frontend Next hacia `/api/analitica/*` del backend FastAPI. Reenvía cualquier sub-path y querystring, inyecta las credenciales de Cloudflare Access + API key, y devuelve el JSON tal cual sin cachear (analíticas que cambian al ritmo de los trades).

Conecta con: front Next (vistas de analítica) → este route → backend `api/routers/analitica.py` en `api.acaquant.com`.
