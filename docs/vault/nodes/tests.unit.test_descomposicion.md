---
id: tests.unit.test_descomposicion
type: module
layer: tests
repo: backend
tags: [module, tests, backend]
path: tests/unit/test_descomposicion.py
---

# tests/unit/test_descomposicion

> Tests del cálculo de descomposición de retorno (Lecap / Boncap / Lecer).

**Archivo:** `tests/unit/test_descomposicion.py`

## Qué hace
Valida el cálculo de atribución de retorno (`api/services/descomposicion_retorno.py`) sin tocar Mongo: interpolación lineal/cuadrática de la curva por plazo y la descomposición de un bono en carry + rolldown + cambio_tasa, para tasa fija (TEM, freq 30d) y CER (TEA, freq 365d). El invariante central que congela: los tres componentes siempre suman el retorno total, y curva quieta ⇒ cambio_tasa=0. Cubre edge cases (período invertido, precios negativos, bono ya vencido) que devuelven None.

Conecta con: importa `_descomponer_un_bono` e `_interpolar` de `api.services.descomposicion_retorno`; red de seguridad de la vista de descomposición de retorno (Lecap/Boncap/Lecer).

## Usa / conecta con →
- [[api.services.descomposicion_retorno]]  ·  _module_
