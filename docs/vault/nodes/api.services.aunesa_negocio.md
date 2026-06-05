---
id: api.services.aunesa_negocio
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\services\aunesa_negocio.py
---

# api/services/aunesa_negocio

> aunesa_negocio.py — service compartido para análisis del endpoint

**Archivo:** `api\services\aunesa_negocio.py`

## Qué hace
Service compartido que procesa el endpoint `/operaciones/consolidadosGenerales` de Aunesa: filtra el ruido (OTC, futuros USDL, integración de garantías), parsea el campo `informacion`, categoriza cada movimiento en ~16 categorías operativas, invierte el signo a perspectiva cliente (+ ingreso / − egreso) y agrupa por comprobante con deduplicaciones específicas (FCI, acreencias). Función principal: `fetch_y_consolidar(fecha, tipos_cuenta)`.

Conecta con: pega a Aunesa (login + consolidadosGenerales); aplica `api.services._negocio_informacion_filter`; lo usan `jobs.negocio_movimientos` (persiste a `CashFlow.NegocioMovimientos`), el endpoint exploratorio `/manager/aunesa/explorar` y `/operaciones/negocio`.

## Usa / conecta con →
- [[api.services._negocio_informacion_filter]]  ·  _module_
- [[config]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.manager.aunesa]]  ·  _module_
- [[jobs.negocio_movimientos]]  ·  _module_
