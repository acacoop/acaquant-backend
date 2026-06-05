---
id: scripts.diag_operadores_actividad
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_operadores_actividad.py
---

# scripts/diag_operadores_actividad

> Diagnóstico read-only: actividad comercial (última op / estado) comparando

**Archivo:** `scripts/diag_operadores_actividad.py`

## Qué hace
Diagnóstico read-only que compara la actividad comercial (última operación / estado por cuenta) usando la fuente vieja (CashFlow.NegocioMovimientos) vs la nueva (CashFlow.Operaciones). Sirve para medir el impacto de migrar la fuente en comercial.py antes de confiar en el cambio: cuántas cuentas pasan de DORMIDA/NUEVA a ACTIVA/ENFRIANDOSE al usar la fuente más completa. Filtra por operador y permite ajustar los umbrales de días. No escribe nada. Se corre con python -m scripts.diag_operadores_actividad [--operador x] [--dias-activa N].

Conecta con: CashFlow.Operaciones, CashFlow.NegocioMovimientos, Clientes.Comitentes vía api.db; reutiliza estado_comercial y match_no_futuros de api.services. Valida la migración de fuente del Tablero Comercial.

## Usa / conecta con →
- [[api.db]]  ·  _module_
- [[api.services._negocio_futuros]]  ·  _module_
- [[api.services.comercial]]  ·  _module_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_
- [[db.CashFlow.Operaciones]]  ·  _collection_
- [[db.Clientes.Comitentes]]  ·  _collection_
