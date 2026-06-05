---
id: scripts.diag_sin_operador
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_sin_operador.py
---

# scripts/diag_sin_operador

> scripts/diag_sin_operador.py — ¿Qué cuentas son el bucket "(sin operador)" del

**Archivo:** `scripts/diag_sin_operador.py`

## Qué hace
Diagnóstico read-only que lista y categoriza las cuentas del bucket "(sin operador)" del ranking comercial, antes de tocar ese ranking. Replica el criterio de informe_comercial (operador_email vacío o cuenta ausente de Comitentes) y, para las que no están en Comitentes, las clasifica con la lógica de jobs/_aum_filters (propia [100]/[101] / contraparte / FCI / OTC) para confirmar que son no-clientes; las "SIN CLASIFICAR" serían clientes reales faltantes del master. No escribe nada. Se corre con python -m scripts.diag_sin_operador [--top N].

Conecta con: CashFlow (movimientos), Clientes.Comitentes; reutiliza api.services.comercial y jobs._aum_filters. Soporte al ranking de volumen por operador.

## Usa / conecta con →
- [[api.services._negocio_futuros]]  ·  _module_
- [[api.services.comercial]]  ·  _module_
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.NegocioMovimientos]]  ·  _collection_
- [[db.Clientes.Comitentes]]  ·  _collection_
- [[jobs._aum_filters]]  ·  _module_
