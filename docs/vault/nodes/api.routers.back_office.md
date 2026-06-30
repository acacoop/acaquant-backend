---
id: api.routers.back_office
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/routers/back_office.py
---

# api/routers/back_office

> Router /api/back-office — sección Back Office.

**Archivo:** `api/routers/back_office.py`

## Qué hace
Router `/api/back-office`: sección Back Office. Por ahora expone solo `titulos-mercado` — qué títulos hay que enviar y recibir hoy contra el mercado, calculando el settlement (ops del día con plazo CI/Inmediato + ops del día hábil anterior a 24hs). Si la fecha no es día hábil devuelve estructura vacía con `mercado_cerrado`.

Conecta con: delega en `api.services.back_office_titulos.get_titulos_mercado` (deriva de `CashFlow.NegocioMovimientos`); requiere identidad vía `api.auth.get_user_email`; lo monta `api.main`.

## Usa / conecta con →
- [[api.auth]]  ·  _module_
- [[api.services]]  ·  _module_
- [[api.services.acreencias]]  ·  _module_
- [[api.services.back_office_titulos]]  ·  _module_
- [[api.services.tenencia_hd]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
