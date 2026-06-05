---
id: tests.unit.test_comercial
type: module
layer: tests
repo: backend
tags: [module, tests, backend]
path: tests/unit/test_comercial.py
---

# tests/unit/test_comercial

> Tests del estado comercial (api/services/comercial.py).

**Archivo:** `tests/unit/test_comercial.py`

## Qué hace
Valida la lógica del Tablero Comercial. Congela el semáforo de actividad (`estado_comercial`): NUEVA si nunca operó, DORMIDA si operó pero fuera de ventana, ACTIVA / ENFRIANDOSE según días sin operar (umbrales 30/90). También verifica la extracción de `id_cuenta` desde el string con corchetes (`[805] MOLLO` → "805") y que el `$match` de volumen filtre por `id_cuenta` indexado (nunca por regex sobre `cuenta`), pesificando ARS+USD sin filtrar por moneda.

Conecta con: blinda `api/services/comercial.py` (`estado_comercial`, `_match_volumen`) y `jobs/negocio_movimientos.py::_extract_id_cuenta`; protege la performance del filtro indexado sobre `CashFlow.NegocioMovimientos`.

## Usa / conecta con →
- [[api.services.comercial]]  ·  _module_
- [[jobs.negocio_movimientos]]  ·  _module_
