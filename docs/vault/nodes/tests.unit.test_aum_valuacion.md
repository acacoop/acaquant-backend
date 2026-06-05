---
id: tests.unit.test_aum_valuacion
type: module
layer: tests
repo: backend
tags: [module, tests, backend]
path: tests/unit/test_aum_valuacion.py
---

# tests/unit/test_aum_valuacion

> Golden tests de la fórmula de valuación AuM (api/services/portfolio.py).

**Archivo:** `tests/unit/test_aum_valuacion.py`

## Qué hace
Golden tests de la fórmula de valuación de AuM. Congela `_valuacion_api`: renta fija (Títulos, Letras, ONs, Fideicomisos, CPD) se valúa cantidad × precio / 100, mientras que FCI y clase OTROS van precio × cantidad directo. También chequea que "FCI" en el nombre de la cartera fuerce el cálculo directo y que renta fija en otras carteras no se confunda. Si alguien rompe esta regla, el AuM de carteras enteras queda mal.

Conecta con: blinda `api/services/portfolio.py::_valuacion_api` (regla documentada en CLAUDE.md). La regla de Futuros (px+1)×cant vive en `jobs/aum.py` y no la cubre este test.

## Usa / conecta con →
- [[api.services.portfolio]]  ·  _module_
