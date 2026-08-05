---
id: quant.stats
type: module
layer: quant
repo: backend
tags: [module, quant, backend]
path: quant/stats.py
---

# quant/stats

> Helpers estadísticos sobre series numéricas.

**Archivo:** `quant/stats.py`

## Qué hace
Helpers estadísticos de propósito general para contextualizar un valor contra su propia historia ("benchmarks dinámicos"): percentil, z-score, detección de tendencia (primer vs último tercio) y `classify_level`, que etiqueta el valor actual como minimo/bajo/medio/alto/maximo o tendencia alcista/bajista. `compute_stats` arma el paquete completo (min/max/media/desvío/percentil/clasificación) tolerante a nulls.

Conecta con: lo importa `api/services/macro.py` (`compute_stats`, `cambio_pct`) para clasificar series macro argentinas (riesgo país, IPC, REM, etc.) relativas a su historia. Función pura, sin Mongo.

## Lo usan (backlinks) ←
- [[api.services.macro_sql]]  ·  _module_
