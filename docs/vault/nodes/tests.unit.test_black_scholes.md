---
id: tests.unit.test_black_scholes
type: module
layer: tests
repo: backend
tags: [module, tests, backend]
path: tests/unit/test_black_scholes.py
---

# tests/unit/test_black_scholes

> Tests de quant/black_scholes.py — pricing, Greeks, IV.

**Archivo:** `tests/unit/test_black_scholes.py`

## Qué hace
Valida el motor de pricing de opciones Black-Scholes: precio (put-call parity ATM, intrínseco en vencimiento, deep ITM), los Greeks (delta ITM/OTM/ATM, gamma y vega siempre positivos, theta del call negativo) y la volatilidad implícita por round-trip (precio sintético con σ conocida → `find_iv` debe recuperarla). También cubre intrínseco/extrínseco y bordes (precio cercano al intrínseco → IV=0).

Conecta con: blinda `quant/black_scholes.py`, el cálculo puro que alimenta el módulo de opciones (`api/services/opciones.py`) y el motor de opciones.

## Usa / conecta con →
- [[quant.black_scholes]]  ·  _module_
