---
id: core.brackets
type: module
layer: core
repo: backend
tags: [module, core, backend]
path: core/brackets.py
---

# core/brackets

> Brackets — entrada LIMIT + salida automática cuando la entrada se llena.

**Archivo:** `core/brackets.py`

## Qué hace
Implementa brackets de trading: orden de entrada LIMIT + salida automática (take-profit) cuando la entrada se llena. Cuando un order report marca la entrada FILLED, el motor de órdenes dispara la salida (side opuesto, misma cantidad, LIMIT al precio definido). Sin stop-loss. Maneja el ciclo de estados PENDING_ENTRY → EXIT_SENT → COMPLETED (y casos de cancelación/rechazo).

Conecta con: escribe/lee `Operaciones.BracketsLive` (sin TTL); lo procesa `engines/motor_ordenes.py`, que observa execution reports de pyRofex y dispara la pata de salida.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.Operaciones]]  ·  _collection_

## Lo usan (backlinks) ←
- [[api.routers.operar]]  ·  _module_
- [[engines.motor_ordenes]]  ·  _module_
