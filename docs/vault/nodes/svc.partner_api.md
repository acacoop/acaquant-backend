---
id: svc.partner_api
type: service
layer: deploy
repo: infra
tags: [service, deploy, infra]
path: deploy\systemd\partner_api.service
---

# systemd: partner_api

> Servicio systemd.

**Archivo:** `deploy\systemd\partner_api.service`

## Qué hace
Servicio systemd que corre la app FastAPI externa `partner_api` (`uvicorn partner_api.main:app`) en `127.0.0.1:8100`. Es una API independiente (no se monta en la API principal) que sirve datos de portfolio a un proveedor externo, expuesta vía nginx en `data.acaquant.com`.

Conecta con: ejecuta `partner_api/main.py`; lee la base `ACAPortfolio.Cartera` (poblada por el job `partner_export`); auth y rate-limit propios. Cliente: el proveedor externo.

_Sin conexiones detectadas mecánicamente._
