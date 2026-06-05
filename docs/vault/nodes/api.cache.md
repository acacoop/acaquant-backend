---
id: api.cache
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/cache.py
---

# api/cache

> Cache in-process para endpoints FastAPI.

**Archivo:** `api/cache.py`

## Qué hace
Cache in-process para handlers de FastAPI vía decorador `@cached(ttl=N)`. Como la API es un único proceso, alcanza un dict con TTL (sin Redis): ahorra un round-trip a Mongo por request repetido dentro de la ventana. La llave es nombre de función + kwargs; el store está acotado (sweep de expiradas + eviction LRU al pasar 512 entradas) para evitar el leak de RAM que tuvo en producción. No cachea respuestas vacías (negative caching off).

Conecta con: lo importan los routers (`cuentas`, `carteras`, etc.) para envolver endpoints; complementa el caching de la capa de servicios.

## Lo usan (backlinks) ←
- [[api.routers.cuentas]]  ·  _module_
- [[api.routers.manager.logs]]  ·  _module_
- [[api.routers.operaciones]]  ·  _module_
- [[api.routers.titulos]]  ·  _module_
- [[api.services._cuentas_filter]]  ·  _module_
- [[api.services.analitica]]  ·  _module_
- [[api.services.argy]]  ·  _module_
- [[api.services.back_office_titulos]]  ·  _module_
- [[api.services.canje]]  ·  _module_
- [[api.services.carry_trade]]  ·  _module_
- [[api.services.comercial]]  ·  _module_
- [[api.services.comparar_inversion]]  ·  _module_
- [[api.services.compliance]]  ·  _module_
- [[api.services.derivados]]  ·  _module_
- [[api.services.descomposicion_retorno]]  ·  _module_
- [[api.services.fair_value]]  ·  _module_
- [[api.services.macro]]  ·  _module_
- [[api.services.mejoras_dispo]]  ·  _module_
- [[api.services.opciones]]  ·  _module_
- [[api.services.order_book]]  ·  _module_
- [[api.services.pnl]]  ·  _module_
- [[api.services.portfolio]]  ·  _module_
- [[api.services.rem]]  ·  _module_
- [[api.services.renta_fija]]  ·  _module_
- [[api.services.repo]]  ·  _module_
- [[api.services.risk]]  ·  _module_
- [[api.services.rv_motor]]  ·  _module_
- [[api.services.scanner]]  ·  _module_
- [[api.services.sensibilidad]]  ·  _module_
- [[api.services.sinteticos]]  ·  _module_
- [[api.services.valuaciones]]  ·  _module_
