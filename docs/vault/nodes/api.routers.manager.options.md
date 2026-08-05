---
id: api.routers.manager.options
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/routers/manager/options.py
---

# api/routers/manager/options

> GET/PUT /api/manager/options/expiries — config del engine de opciones (SQL-native).

**Archivo:** `api/routers/manager/options.py`

## Qué hace
Sub-router `/api/manager/options/expiries` — configura qué vencimientos sigue el motor de opciones GGAL. GET lee `Opciones.Metadata` (vencimientos disponibles publicados por el engine + los activos elegidos por el user); si no hay activos, el engine auto-pickea el próximo. PUT persiste la selección, que el engine toma en su próximo chequeo (~5 min). Admin-only.

Conecta con: lee/escribe `Opciones.Metadata` (doc `type=config`); el productor/consumidor del otro lado es `engines.options`. Lo consume la tab OPTIONS de la manager-view.

## Usa / conecta con →
- [[api.routers.manager._common]]  ·  _module_
- [[core]]  ·  _module_
- [[core.pg_mirror]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.routers.manager]]  ·  _module_
