---
id: api.routers.derivados_sinteticos
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\routers\derivados_sinteticos.py
---

# api/routers/derivados_sinteticos

> Router /api/derivados/sinteticos — sintéticos LECAP / DLK + futuro DLR.

**Archivo:** `api\routers\derivados_sinteticos.py`

## Qué hace
Router HTTP `/api/derivados/sinteticos` — un único GET de solo lectura que devuelve las dos tablas de sintéticos (long-LECAP y short-DLK) armadas matcheando cada instrumento contra un futuro DLR vigente por año-mes de vencimiento. Thin wrapper: toda la lógica vive en el service. Acceso por el gate genérico de `/api/derivados/*` (abierto a los 3 roles).

Conecta con: service `api.services.sinteticos::get_sinteticos`; auth `get_user_email`. Lo consume la vista de derivados/sintéticos en acaquant-web.

## Usa / conecta con →
- [[api.auth]]  ·  _module_
- [[api.services.sinteticos]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
