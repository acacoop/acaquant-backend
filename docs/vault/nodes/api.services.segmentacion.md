---
id: api.services.segmentacion
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/segmentacion.py
---

# api/services/segmentacion

> Clasificación patrimonial de clientes (escribe a `Clientes.Comitentes.nivel_3`).

**Archivo:** `api/services/segmentacion.py`

## Qué hace
Clasificación patrimonial de clientes en 6 segmentos: 3 de Personas Humanas por umbral USD (vía MEP) y 3 de Personas Jurídicas por umbral en UVAs, más la excepción que fuerza PJ GRANDE para FCI/contrapartes. La función núcleo `clasificar_nivel_3` es pura (recibe inputs, devuelve el label en MAYÚSCULAS con prefijo PH/PJ; devuelve None si falta data). PH/PJ se distingue por `tipo_cliente`.

Conecta con: el label se escribe en `Clientes.Comitentes.nivel_3`; lee umbrales de cupo y el valor UVA. Lo usan el job `jobs.segmentar_patrimonial` (re-clasifica todo) y el endpoint manager `bulk-fondeo` / PATCH (re-clasifica las cuentas tocadas).

## Usa / conecta con →
- [[db.CashFlow.Contrapartes]]  ·  _collection_
- [[db.CashFlow.Productores]]  ·  _collection_

## Lo usan (backlinks) ←
- [[api.routers.manager.clientes]]  ·  _module_
- [[api.routers.manager.comercial]]  ·  _module_
- [[jobs.segmentar_patrimonial]]  ·  _module_
- [[scripts.backfill_contrapartes_pj_grande]]  ·  _module_
