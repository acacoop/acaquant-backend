---
id: scripts.diag_fci_fuente
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_fci_fuente.py
---

# scripts/diag_fci_fuente

> scripts/diag_fci_fuente.py — ¿El FCI de Operaciones es confiable, o debería

**Archivo:** `scripts/diag_fci_fuente.py`

## Qué hace
Diagnóstico read-only que compara las dos fuentes de FCI (NegocioMovimientos crudo vs Operaciones, lo que ve la vista) para decidir si arreglar la inyección de FCI bilateral o leer todo desde NegocioMovimientos. Tres bloques: FCI por categoria×prefijo de comprobante en la fuente cruda, qué FCI hay en Operaciones (bilateral inyectado + normal), y reconciliación de los últimos N días (cuántos comprobantes que el job debería inyectar faltan = hueco real). Por default mide solo hoy. Se corre con python -m scripts.diag_fci_fuente [--fecha YYYY-MM-DD].
Conecta con: CashFlow.NegocioMovimientos, CashFlow.Operaciones, core.mongo. Diagnostica el job fci_bilateral (que tuvo COLLSCAN y estuvo pausado).

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_
- [[db.CashFlow.Operaciones]]  ·  _collection_
