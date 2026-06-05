---
id: tests.unit.test_curvas_math
type: module
layer: tests
repo: backend
tags: [module, tests, backend]
path: tests/unit/test_curvas_math.py
---

# tests/unit/test_curvas_math

> Tests de funciones cuantitativas en engines/curvas.py.

**Archivo:** `tests/unit/test_curvas_math.py`

## Qué hace
Suite que blinda el núcleo cuantitativo de `engines/curvas.py`: XIRR, duration de Macaulay, convexity, montos de flujo (tasa fija absoluta y CER porcentual), parseo de fechas, búsqueda de CER con fallback de hasta 7 días y navegación de días hábiles (incluido settlement T-10). Congela los invariantes (cero cupón = maturity, convexity crece con plazo) que sostienen el cálculo de TEA/TNA de toda la renta fija.

Conecta con: importa `engines.curvas` (xirr, macaulay_duration, convexity, monto_flujo, monto_flujo_cer, get_cer_*); es la red de seguridad del motor de curvas que alimenta MarketSnapshot.

## Usa / conecta con →
- [[engines.curvas]]  ·  _module_
