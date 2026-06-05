---
id: scripts.diag_actividad_mensual
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_actividad_mensual.py
---

# scripts/diag_actividad_mensual

> diag_actividad_mensual.py — read-only: rango y volumen del histórico de

**Archivo:** `scripts/diag_actividad_mensual.py`

## Qué hace
Diagnóstico de solo lectura que mide, desde CashFlow.NegocioMovimientos (solo boletos operativos), la profundidad del histórico de cuentas activas: primer y último mes con actividad y, por mes, cuántas cuentas distintas operaron y cuántos boletos hubo. Sirve para confirmar hasta dónde llega el histórico antes de backfillear y para sanity-check de lo que persiste jobs/actividad_mensual.py. No escribe. Se corre `python -m scripts.diag_actividad_mensual`.
Conecta con: CashFlow.NegocioMovimientos (lee), categorías de api.services.comercial, contraparte de jobs.actividad_mensual, core.mongo (read).

## Usa / conecta con →
- [[api.services.comercial]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_
