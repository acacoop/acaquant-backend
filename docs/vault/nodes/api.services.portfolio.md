---
id: api.services.portfolio
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/portfolio.py
---

# api/services/portfolio

> Capa de servicio — portfolio / AuM / FCI: helpers PUROS compartidos.

**Archivo:** `api/services/portfolio.py`

## Qué hace
Capa de servicio de portfolio / AuM / FCI: lógica pura de valuación por cuenta sobre las copias `PortfolioAPI`/`TitulosAPI` y `Valuaciones`. Centraliza la regla de valuación en `_valuacion_api` (renta fija ÷100, FCI/otros directo, futuros (precio+1)×cant). Excluye de la vista AuM ciertas cuentas (ej. 255 trading propia) aunque se sigan capturando. Funciones cacheadas.

Conecta con: lee `Valuaciones.Assets`/`AuM`, `PortfolioAPI`, `TitulosAPI` y `Trading`; usa `_cuentas_filter` y `_mep`. El router `api/routers/carteras.py` es su thin wrapper; alimenta las vistas de AuM y el KPI "TOTAL FCI HOY".

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[api.services.assets_sql]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.services.comercial]]  ·  _module_
