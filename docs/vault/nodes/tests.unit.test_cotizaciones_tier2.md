---
id: tests.unit.test_cotizaciones_tier2
type: module
layer: tests
repo: backend
tags: [module, tests, backend]
path: tests/unit/test_cotizaciones_tier2.py
---

# tests/unit/test_cotizaciones_tier2

> Tests unitarios de las tools Tier 2 (snapshot_historico, pendiente, liquidez).

**Archivo:** `tests/unit/test_cotizaciones_tier2.py`

## Qué hace
Valida las tools analíticas Tier 2 sobre renta fija con Mongo mockeado (sin DB real). Cubre `snapshot_curva_historico` (devuelve solo bonos que operaron ese día, curva/fecha inválida → vacío), `calcular_pendiente_curva` (largo menos corto en bps, empinamiento/aplanamiento vs fecha de comparación, errores por curva/métrica inválida o datos insuficientes) y `liquidez_secundario` (clasifica baja/media/anómala según ratio vs promedio, excluyendo el día actual del promedio).

Conecta con: blinda `api/services/analitica.py`; estas tools se exponen vía el router Analítica y el MCP server, leyendo `Trading.Curvas` y `Trading.TimeSales`.

## Usa / conecta con →
- [[api.cache]]  ·  _module_
- [[api.services.analitica]]  ·  _module_
- [[db.Trading.Curvas]]  ·  _collection_
- [[db.Trading.TimeSales]]  ·  _collection_
