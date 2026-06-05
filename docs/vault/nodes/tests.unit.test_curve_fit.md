---
id: tests.unit.test_curve_fit
type: module
layer: tests
repo: backend
tags: [module, tests, backend]
path: tests/unit/test_curve_fit.py
---

# tests/unit/test_curve_fit

> Tests del fit cuadrático puro (sin Mongo).

**Archivo:** `tests/unit/test_curve_fit.py`

## Qué hace
Valida el ajuste cuadrático puro (`quant/curve_fit.py::fit_quadratic`) usado para fittear la curva TEA-vs-duration: que recupere coeficientes exactos con R²=1 sobre datos perfectos, prediga bien, devuelva None con menos de 3 puntos o matriz singular, y que el R² quede en [0,1]. Incluye un sanity con datos reales de tasa fija.

Conecta con: importa `quant.curve_fit`; es la red de seguridad del fit que consume `jobs/fair_value.py` para el módulo Fair Value relativo intra-curva.

## Usa / conecta con →
- [[quant.curve_fit]]  ·  _module_
