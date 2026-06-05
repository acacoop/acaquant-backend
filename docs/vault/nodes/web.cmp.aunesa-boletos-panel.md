---
id: web.cmp.aunesa-boletos-panel
type: component
layer: web-component
repo: frontend
tags: [component, web-component, frontend]
path: src\components\aunesa-boletos-panel.tsx
---

# web/components/aunesa-boletos-panel

**Archivo:** `src\components\aunesa-boletos-panel.tsx`

## Qué hace
Tab BOLETOS dentro de MANAGER → AUNESA, con sub-tabs FALTANTES (lista boletos sin arancel asignado, con resumen por categoría/op y detalle) y BACKFILL (dispara el matching de aranceles contra Aunesa). Filtra por rango de fechas e `id_cuenta`; excluye futuros DLR en backend.

Conecta con: pollea `GET /api/manager/aunesa/boletos/faltantes`; los datos salen de `CashFlow.NegocioMovimientos`. Solo manager.

## Usa / conecta con →
- [[api.routers.manager.aunesa]]  ·  _module_
