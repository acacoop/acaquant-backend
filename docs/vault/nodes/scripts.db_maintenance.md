---
id: scripts.db_maintenance
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/db_maintenance.py
---

# scripts/db_maintenance

> db_maintenance.py — cambios de optimización DBA. Idempotente, dry-run por default.

**Archivo:** `scripts/db_maintenance.py`

## Qué hace
Herramienta DBA reusable que aplica las optimizaciones surgidas de la auditoría: Fase 1 crea índices faltantes y dropea redundantes (cubiertos por un compuesto), Fase 2 aplica retención TTL a 90 días sobre logs/auditorías que crecen sin límite y time-series de mercado (Trading.TimeSales, Opciones.Data). Idempotente, dry-run por default; el TTL detecta automáticamente time-series (collMod) vs colección normal (TTL index) y saltea si el campo datetime es ambiguo. Se corre `python -m scripts.db_maintenance [--apply] [--solo indices|ttl]`.
Conecta con: índices/TTL sobre PortfolioAPI, Manager, Market, Opciones, Trading.TimeSales y varias *Audit; insumo de scripts/audit_db; core.mongo.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.Operaciones]]  ·  _collection_
- [[db.Manager.RoleAudit]]  ·  _collection_
- [[db.Trading.TimeSales]]  ·  _collection_
