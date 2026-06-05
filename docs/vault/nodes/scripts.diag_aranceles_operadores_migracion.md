---
id: scripts.diag_aranceles_operadores_migracion
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_aranceles_operadores_migracion.py
---

# scripts/diag_aranceles_operadores_migracion

> Diag READ-ONLY: impacto de migrar los ARANCELES de operadores de

**Archivo:** `scripts/diag_aranceles_operadores_migracion.py`

## Qué hace
Diagnóstico de solo lectura que compara, por operador, el Σ arancel total y del mes según la fuente vieja (NegocioMovimientos, incompleta) vs la nueva (CashFlow.Operaciones, completa, sin Cierre ni etapa=solicitud), para ver el delta que verá la mesa ANTES de migrar el código de comercial.py. No escribe. Se corre `python -m scripts.diag_aranceles_operadores_migracion`.
Conecta con: CashFlow.Operaciones y CashFlow.NegocioMovimientos (lee), Clientes.Comitentes (operador por cuenta), api.services._negocio_futuros, core.mongo (read).

## Usa / conecta con →
- [[api.services._negocio_futuros]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_
- [[db.CashFlow.Operaciones]]  ·  _collection_
- [[db.Clientes.Comitentes]]  ·  _collection_
