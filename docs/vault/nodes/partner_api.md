---
id: partner_api
type: module
layer: partner_api
repo: backend
tags: [module, partner_api, backend]
path: partner_api/__init__.py
---

# partner_api/__init__

> partner_api — API externa de datos de portfolio para un proveedor.

**Archivo:** `partner_api/__init__.py`

## Qué hace
Paquete raíz del servicio externo de datos de portfolio para un proveedor. Es una app FastAPI SEPARADA de la mesa (proceso y puerto propios): solo lee `ACAPortfolio.Cartera`, con auth propia (usuario/password → JWT) independiente de Cloudflare Access. Diseñado para aislar: si lo atacan o se cae, `api.acaquant.com` no se entera.

Conecta con: la colección `ACAPortfolio.Cartera` (poblada por el cron `jobs/partner_export.py` con las cuentas de `config.PARTNER_EXPORT_CUENTAS`); corre como systemd `partner_api.service` en `127.0.0.1:8100`, expuesto por nginx en `data.acaquant.com`. Doc: `docs/PARTNER_API.md`.

## Lo usan (backlinks) ←
- [[partner_api.main]]  ·  _module_
