---
id: scripts.healthcheck_mongo
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/healthcheck_mongo.py
---

# scripts/healthcheck_mongo

> healthcheck_mongo.py — ping rápido a las 3 conexiones Mongo del proyecto.

**Archivo:** `scripts/healthcheck_mongo.py`

## Qué hace
Herramienta reusable que hace un ping rápido a las 3 conexiones Mongo del proyecto (MONGO_URI rw, MONGO_URI_READ ro secondaryPreferred, PARTNER_MONGO_URI de la Partner API) con timeout corto e imprime OK/FAIL por cada una. Pensada para correr justo después de tocar la whitelist de Atlas (Network Access) y detectar en 5 segundos si alguna quedó bloqueada, en vez de enterarse por un 502 de la mesa. Se corre con `python -m scripts.healthcheck_mongo`.
Conecta con: valida las URIs que consumen core.mongo y partner_api; chequeo previo/posterior a cambios de Atlas.

_Sin conexiones detectadas mecánicamente._
