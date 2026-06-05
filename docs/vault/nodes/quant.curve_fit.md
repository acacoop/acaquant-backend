---
id: quant.curve_fit
type: module
layer: quant
repo: backend
tags: [module, quant, backend]
path: quant\curve_fit.py
---

# quant/curve_fit

> curve_fit.py — Ajuste de curva por mínimos cuadrados.

**Archivo:** `quant\curve_fit.py`

## Qué hace
Ajuste de curva por mínimos cuadrados (OLS): fittea una cuadrática TEA(d) = β₀ + β₁·d + β₂·d² sobre puntos de TEA vs duration. Solución cerrada por Cramer sobre la matriz 3×3, sin scipy; devuelve los betas, el R² y un `.predict(duration)`. Elegida sobre log o Nelson-Siegel por capturar humps con pocos puntos (7-15 bonos) sin sobreajustar. Devuelve None con <3 puntos o matriz singular.

Conecta con: lo importa `jobs/fair_value.py`, que le pasa la curva de bonos del día y usa la cuadrática ajustada como "valor justo" para medir residuos y z-scores intra-curva. Función pura, no toca Mongo.

## Lo usan (backlinks) ←
- [[jobs.fair_value]]  ·  _module_
