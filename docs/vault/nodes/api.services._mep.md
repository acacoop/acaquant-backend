---
id: api.services._mep
type: module
layer: api
repo: backend
tags: [module, api, backend]
path: api/services/_mep.py
---

# api/services/_mep

> Helper compartido — devuelve el MEP histórico para una fecha dada.

**Archivo:** `api/services/_mep.py`

## Qué hace
Helper que devuelve el MEP histórico para una fecha dada: el último valor con `timestamp <= fin-del-día(fecha)`, o `None` si no hay docs anteriores. Sirve para pesificar/dolarizar: en `Valuaciones.AuM` la valuación está siempre en ARS, así que para mostrar en USD basta dividir por este MEP.

Conecta con: lee `Valuaciones.Dolar` (poblada por el script de PC oficina); lo usan los services de valuaciones/PnL como fallback cuando un boleto no trae su `mep` snapshot.

## Usa / conecta con →
- [[core]]  ·  _module_
- [[core.dolar_sql]]  ·  _module_

## Lo usan (backlinks) ←
- [[api.services.cashflow_sql]]  ·  _module_
- [[api.services.operaciones_informes]]  ·  _module_
- [[api.services.pnl]]  ·  _module_
- [[api.services.pnl_sql]]  ·  _module_
- [[api.services.tenencia_hd]]  ·  _module_
- [[api.services.valuaciones]]  ·  _module_
- [[jobs.negocio_movimientos]]  ·  _module_
