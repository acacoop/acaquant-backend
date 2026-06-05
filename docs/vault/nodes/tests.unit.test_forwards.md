---
id: tests.unit.test_forwards
type: module
layer: tests
repo: backend
tags: [module, tests, backend]
path: tests/unit/test_forwards.py
---

# tests/unit/test_forwards

> Tests de la matriz de tasas forward en engines/forwards.py.

**Archivo:** `tests/unit/test_forwards.py`

## Qué hace
Valida la matriz de tasas forward (`engines/forwards.py::calcular_matriz`): con un solo bono publica la tasa spot sin matriz, descarta los que no tienen TEA+duration, ordena por duration ascendente y arma solo forwards de corto a largo. Congela la fórmula (curva plana ⇒ forward = spot; caso conocido (1.25²/1.20)−1) que la UI usa para mostrar tasas forward implícitas.

Conecta con: importa `engines.forwards`; red de seguridad del motor de forwards que lee TEA por ticker desde MarketSnapshot.

## Usa / conecta con →
- [[engines.forwards]]  ·  _module_
