---
id: partner_api.ratelimit
type: module
layer: partner_api
repo: backend
tags: [module, partner_api, backend]
path: partner_api\ratelimit.py
---

# partner_api/ratelimit

> Rate limiter del partner_api — slowapi, keyeado por IP del cliente.

**Archivo:** `partner_api\ratelimit.py`

## Qué hace
Rate limiter compartido del servicio, basado en slowapi y keyeado por la IP real del cliente. Detrás de Cloudflare+nginx, la IP verdadera viene en `CF-Connecting-IP` (helper `client_ip`); si no, cae a la IP de la conexión. Limita tanto la fuerza bruta sobre `/v1/token` como el martilleo de los endpoints de datos (límite por defecto 120/hora).

Conecta con: exporta `limiter` y `client_ip`, usados por `partner_api.auth`, `partner_api.routes` y `partner_api.main` (registra el handler de `RateLimitExceeded`).

## Lo usan (backlinks) ←
- [[partner_api.auth]]  ·  _module_
- [[partner_api.main]]  ·  _module_
- [[partner_api.routes]]  ·  _module_
