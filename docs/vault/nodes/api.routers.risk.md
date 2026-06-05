---
id: api.routers.risk
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/routers/risk.py
---

# api/routers/risk

> Router /api/risk — datos de cuenta del broker (saldos, posiciones, márgenes).

**Archivo:** `api/routers/risk.py`

## Qué hace
Router de datos de cuenta del broker (información sensible). Expone saldo ARS+USD disponible por rueda (`/account/saldo`), el report crudo del broker, y posiciones —simples y detalladas por tipo de instrumento—. Cada endpoint resuelve la cuenta con scope de grupos antes de consultar.

Conecta con: delega en `api.services.risk` (que consulta a ROFEX vía sesión del broker, con cache corto); scope de grupos (`verificar_account`); gate RBAC módulo `operaciones` (admin+trader); lo consumen la UI Dólar MEP y vistas de riesgo del frontend.

## Usa / conecta con →
- [[api.auth]]  ·  _module_
- [[api.services._grupos_scope]]  ·  _module_
- [[api.services.risk]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
- [[web.lib.proxy]]  ·  _lib_
