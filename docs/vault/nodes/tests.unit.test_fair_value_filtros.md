---
id: tests.unit.test_fair_value_filtros
type: module
layer: tests
repo: backend
tags: [module, tests, backend]
path: tests/unit/test_fair_value_filtros.py
---

# tests/unit/test_fair_value_filtros

> Tests de los filtros del universo del fit (sin Mongo).

**Archivo:** `tests/unit/test_fair_value_filtros.py`

## Qué hace
Valida los filtros del universo del fit Fair Value (`jobs/fair_value.py::_en_universo_fit`) sin Mongo: descarta bonos con menos de 15 días al vto, volumen bajo (<50M), TEA/duration nulos, recién emitidos (<5 días, microestructura ruidosa) y, en CER, los que tienen cupón. Define qué bonos entran a la regresión cuadrática que detecta caro/barato intra-curva.

Conecta con: importa `jobs.fair_value`; red de seguridad del filtro de universo que precede al `fit_quadratic` del módulo Fair Value.

## Usa / conecta con →
- [[jobs.fair_value]]  ·  _module_
