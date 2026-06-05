---
id: scripts.diag_decimal_drift
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_decimal_drift.py
---

# scripts/diag_decimal_drift

> Mide el drift de redondeo float vs Decimal en los totales de AuM.

**Archivo:** `scripts/diag_decimal_drift.py`

## Qué hace
Diagnóstico read-only que mide cuánto difiere sumar las posiciones de una cuenta en float (lo que hace hoy el motor) vs en Decimal, sobre el último snapshot de Valuaciones.AuM agrupado por id_cuenta. Responde si vale la pena migrar el motor a Decimal: si el drift es ~$0.00 no justifica el riesgo, si es material (pesos) sí. Reporta el peor caso y el agregado. Se corre con python -m scripts.diag_decimal_drift [--top 20].
Conecta con: Valuaciones.AuM (lectura), core.mongo. One-shot de decisión técnica (EXT-MONEY1) sobre el motor de valuaciones.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.Valuaciones.AuM]]  ·  _collection_
