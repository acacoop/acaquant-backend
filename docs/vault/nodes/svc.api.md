---
id: svc.api
type: service
layer: deploy
repo: infra
tags: [service, deploy, infra]
path: deploy/systemd/api.service
---

# systemd: api

> Servicio systemd.

**Archivo:** `deploy/systemd/api.service`

## Qué hace
Servicio systemd siempre-activo que corre la API FastAPI (`uvicorn api.main:app`) escuchando en `127.0.0.1:8000`. Es el backend que sirve a la web acaquant (`api.acaquant.com`) y monta también el sub-app MCP. Expuesto al exterior vía nginx + Cloudflare Access.

Conecta con: ejecuta `api/main.py` (todos los routers + servicios); lee/escribe casi todas las bases Mongo; consumido por acaquant-web en Vercel y por el MCP server. Se reinicia con `systemctl restart api.service` tras cada deploy.

_Sin conexiones detectadas mecánicamente._
