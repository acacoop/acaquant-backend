---
id: api.services.compliance
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\services\compliance.py
---

# api/services/compliance

> Compliance — compara el operador asignado por la mesa vs el que reporta Aunesa.

**Archivo:** `api\services\compliance.py`

## Qué hace
Para el rol compliance: cruza en vivo el operador asignado por la mesa en nuestra base (`Clientes.Comitentes.operador_email`, editado a mano) contra el que Aunesa reporta en `cuentas/listadoCuentas`. Como el operador de Aunesa solo se copia al crear la cuenta y nunca se vuelve a pisar, con el tiempo divergen. Marca cada fila como ok / distinto / falta_en_nuestra_base / falta_en_aunesa. No persiste nada; cache 5 min.

Conecta con: lee `Clientes.Comitentes` y pega en vivo a Aunesa vía `core.aunesa`; lo consume el sub-router `/api/manager/compliance`.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[api.db]]  ·  _module_
- [[core]]  ·  _module_
- [[core.aunesa]]  ·  _module_
- [[db.Clientes.Comitentes]]  ·  _collection_

## Lo usan (backlinks) ←
- [[api.routers.manager.compliance]]  ·  _module_
