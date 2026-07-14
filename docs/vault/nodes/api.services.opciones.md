---
id: api.services.opciones
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\services\opciones.py
---

# api/services/opciones

> Capa de servicio — opciones: helpers puros + mutación de tasa (SQL-native).

**Archivo:** `api\services\opciones.py`

## Qué hace
Capa de servicio de opciones GGAL: arma la chain (strikes con bid/offer/last/greeks/IV), la metadata, los trades históricos y permite actualizar la tasa libre de riesgo. Filtra solo opciones con tick del día (las ilíquidas conservan precio viejo y se descartan). Casi todo es solo-lectura; la única escritura es la tasa.

Conecta con: lee la DB `Opciones` (poblada por `engines.options` vía WS y `jobs.options_rollup` al cierre); `update_opciones_tasa` escribe en `Opciones.Metadata.config` y limpia el cache. Lo invoca el router de opciones del módulo derivados.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[core]]  ·  _module_
- [[core.pg_mirror]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.cotizaciones]]  ·  _module_
- [[api.services.opciones_sql]]  ·  _module_
