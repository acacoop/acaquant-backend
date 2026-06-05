---
id: scripts.diag_mongo_users
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_mongo_users.py
---

# scripts/diag_mongo_users

> diag_mongo_users.py — qué usuario de Mongo usa cada URI del proyecto.

**Archivo:** `scripts/diag_mongo_users.py`

## Qué hace
Herramienta de auditoría read-only que muestra qué usuario y host de Atlas usa cada URI del proyecto. Lee el .env de la raíz y parsea MONGO_URI (cliente RW de app), MONGO_URI_READ (cliente RO de la API) y PARTNER_MONGO_URI (la app partner_api), imprimiendo user y host sin exponer nunca el password. Sirve para planear rotación de credenciales sin entrar a Atlas (si dos vars comparten user, rotás ambos). Se corre con python -m scripts.diag_mongo_users.

Conecta con: el .env de la raíz (las URIs que carga core.mongo) y partner_api. No toca Mongo. Insumo de la auditoría de secretos.

_Sin conexiones detectadas mecánicamente._
