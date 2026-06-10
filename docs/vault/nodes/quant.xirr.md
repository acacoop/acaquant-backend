---
id: quant.xirr
type: module
layer: quant
repo: backend
tags: [module, quant, backend]
path: quant/xirr.py
---

# quant/xirr

> xirr.py — TIR.NO.PER de Excel (XIRR / IRR para flujos en fechas

**Archivo:** `quant/xirr.py`

## Qué hace
Implementa la TIR.NO.PER de Excel (XIRR): dado un set de cashflows con fechas irregulares, resuelve la TEA implícita que hace NPV = 0. Usa Newton-Raphson con damping y guess 10%, con fallback a bisección si diverge; rango amplio (hasta TEA 1.000.000%) para cuentas de alta rotación con inflación argentina. Devuelve None si no converge o los flujos son del mismo signo. Solo stdlib.

Conecta con: lo importa `api/services/valuaciones.py` (como `_xirr`) para calcular el rendimiento anualizado por cuenta a partir de aportes/retiros y valuación. Función pura, sin Mongo ni dependencias externas.

## Lo usan (backlinks) ←
- [[api.services.valuaciones]]  ·  _module_
