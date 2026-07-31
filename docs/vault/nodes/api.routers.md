---
id: api.routers
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\routers\__init__.py
---

# api/routers/__init__

**Archivo:** `api\routers\__init__.py`

## Qué hace
Paquete de routers HTTP de la API (`api/routers/`). Cada submódulo es un thin wrapper que parsea query params, aplica auth/scoping y delega en `api/services/*`; nada de lógica de negocio acá. El `__init__.py` está vacío (solo marca el paquete); incluye además el sub-paquete `manager/` con los routers de administración.

Conecta con: todos los routers los importa y monta `api.main`; cada uno delega en su service homónimo de `api.services`.

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
