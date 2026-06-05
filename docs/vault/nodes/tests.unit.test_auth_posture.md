---
id: tests.unit.test_auth_posture
type: module
layer: tests
repo: backend
tags: [module, tests, backend]
path: tests/unit/test_auth_posture.py
---

# tests/unit/test_auth_posture

> Test del fail-closed de auth al boot (EXT-AUTH1, api/main.py).

**Archivo:** `tests/unit/test_auth_posture.py`

## Qué hace
Valida el fail-closed de autenticación al boot de la API (chequeo EXT-AUTH1). Confirma que si `ENV=prod` y falta `API_KEY`, la API NO arranca (lanza RuntimeError); que con API_KEY + config de Cloudflare Access presente arranca sin error; y que en modo `dev` es permisivo y no rompe el entorno local aunque falte la key.

Conecta con: blinda `api/main.py::_validar_postura_auth`; es la red de seguridad que evita levantar producción sin auth configurada.

## Usa / conecta con →
- [[api.main]]  ·  _module_
