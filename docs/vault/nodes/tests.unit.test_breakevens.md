---
id: tests.unit.test_breakevens
type: module
layer: tests
repo: backend
tags: [module, tests, backend]
path: tests/unit/test_breakevens.py
---

# tests/unit/test_breakevens

> Tests de engines/breakevens.py — cálculo de breakeven CER/Lecap.

**Archivo:** `tests/unit/test_breakevens.py`

## Qué hace
Valida el cálculo de breakeven de inflación CER vs Lecap. Verifica que descarte pares con menos de 30 días al vencimiento, que con paridad CER 100% el breakeven mensual iguale al TEM del Lecap, un caso de cálculo manual completo (retorno acumulado → inflación implícita → breakeven mensual anualizado), que sin TEM o paridad no calcule el breakeven, y que la numeración de los pares sea incremental.

Conecta con: blinda `engines/breakevens.py::calcular_breakevens` (la fórmula Buscar Objetivo documentada en CLAUDE.md); lo consume el módulo de breakevens vía MarketSnapshot/Curvas.

## Usa / conecta con →
- [[engines.breakevens]]  ·  _module_
