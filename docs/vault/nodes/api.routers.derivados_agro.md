---
id: api.routers.derivados_agro
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api\routers\derivados_agro.py
---

# api/routers/derivados_agro

> Router /api/derivados/agro — Pase Agro + Estrategias + Cámara + Mejoras Dispo.

**Archivo:** `api\routers\derivados_agro.py`

## Qué hace
Router HTTP `/api/derivados/agro/*` para el módulo Agro Rosario (Trigo/Maíz/Soja): expone la tabla Pase Agro, la cadena de opciones, el simulador de estrategias de cobertura (put sintético / long put), la Cámara Arbitral de Cereales (5 cereales) y Mejoras Precio Disponible. Algunos GET son abiertos a los 3 roles; los PATCH (pizarra, cámara) exigen el módulo `agro` y dejan el email en audit. Es solo plumbing HTTP: delega todo a los services.

Conecta con: services `derivados_agro`, `camara_cereales`, `mejoras_dispo`; auth `require_module("agro")` + `get_user_email`. Vía los services lee `Trading.AgroSnapshot` y `Trading.AgroOpcionesSnapshot` (poblados por motor_agro / motor_agro_opciones) y persiste inputs manuales de pizarra/cámara con su audit. Lo consume la vista Agro de acaquant-web.

## Usa / conecta con →
- [[api.auth]]  ·  _module_
- [[api.services]]  ·  _module_
- [[api.services.agro_cobertura]]  ·  _module_
- [[api.services.agro_sql]]  ·  _module_
- [[api.services.camara_cereales]]  ·  _module_
- [[api.services.derivados_agro]]  ·  _module_
- [[core.eikon_chicago]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.main]]  ·  _module_
