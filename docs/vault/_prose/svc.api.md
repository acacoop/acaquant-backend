Servicio systemd siempre-activo que corre la API FastAPI (`uvicorn api.main:app`) escuchando en `127.0.0.1:8000`. Es el backend que sirve a la web acaquant (`api.acaquant.com`) y monta también el sub-app MCP. Expuesto al exterior vía nginx + Cloudflare Access.

Conecta con: ejecuta `api/main.py` (todos los routers + servicios); lee/escribe casi todas las bases Mongo; consumido por acaquant-web en Vercel y por el MCP server. Se reinicia con `systemctl restart api.service` tras cada deploy.
