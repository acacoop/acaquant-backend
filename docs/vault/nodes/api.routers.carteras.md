---
id: api.routers.carteras
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/routers/carteras.py
---

# api/routers/carteras

> Router Portfolio — thin wrappers sobre `api.services.portfolio`.

**Archivo:** `api/routers/carteras.py`

## Qué hace
Router `/api/portfolio`: endpoints de portfolio / AuM / PnL. Thin wrappers sobre `api.services.portfolio` y `api.services.pnl`; la lógica de joins, agregación y valuación vive en el service. Aplica scoping de grupos (Fase 2): cada endpoint resuelve las cuentas visibles del usuario y, en la vista AUM, intersecta además con el filtro madre por operador.

Conecta con: delega en services `portfolio` y `pnl`; usa `api.services._grupos_scope` para el scope de cuentas y `api.services.comercial._cuentas_de_operador` para el filtro por operador; lo monta `api.main`.

## Usa / conecta con →
- [[api.services]]  ·  _module_
- [[api.services._grupos_scope]]  ·  _module_
- [[api.services.comercial]]  ·  _module_
- [[api.services.pnl]]  ·  _module_
- [[api.services.portfolio]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
