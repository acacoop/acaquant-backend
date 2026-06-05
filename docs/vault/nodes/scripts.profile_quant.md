---
id: scripts.profile_quant
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/profile_quant.py
---

# scripts/profile_quant

> profile_quant.py — Microbenchmark del cálculo PURO de quant/.

**Archivo:** `scripts/profile_quant.py`

## Qué hace
Herramienta reusable de microbenchmark del cálculo puro de quant/: mide con timeit cada función pura (black_scholes, curve_fit, rolling_stats, stats, xirr, pivot_points) sobre datasets sintéticos del tamaño real de la mesa, para responder con números si el cómputo justifica vectorizar/numba/C++/Rust. No toca Mongo ni red (las funciones que leen Mongo quedan fuera a propósito: su costo es I/O). Contrasta contra budgets de referencia (~50ms request, ~80ms query). Se corre con `python -m scripts.profile_quant`.

Conecta con: quant.* (black_scholes, curve_fit, pivot_points, rolling_stats, stats, xirr); complementa scripts.perf_sweep para separar CPU de I/O.

## Usa / conecta con →
- [[quant]]  ·  _module_
- [[quant.black_scholes]]  ·  _module_
- [[quant.curve_fit]]  ·  _module_
- [[quant.pivot_points]]  ·  _module_
- [[quant.rolling_stats]]  ·  _module_
- [[quant.stats]]  ·  _module_
- [[quant.xirr]]  ·  _module_
