---
id: scripts.diag_persona_datos
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_persona_datos.py
---

# scripts/diag_persona_datos

> diag_persona_datos.py — read-only de Aunesa GET /api/personas/datosPersona.

**Archivo:** `scripts/diag_persona_datos.py`

## Qué hace
Diagnóstico read-only contra Aunesa GET /api/personas/datosPersona que dumpea el KYC completo de una persona (patrimonio, procedencia/medio de fondeo, declaraciones PEP/UIF/FATCA, accionistas) para confirmar si figura el "límite de fondeo" y dónde. Dado --cuenta, primero pega a listadoCuentas, extrae los candidatos (tipoId, id) de titular y personas relacionadas, y llama a datosPersona por cada uno; también acepta la persona directa con --tipo-id/--id. Solo hace auth + GET, no escribe. Se corre con python -m scripts.diag_persona_datos --cuenta 805.

Conecta con: API del custodio vía core.aunesa (endpoints cuentas y personas). Insumo para la segmentación patrimonial (cupo / límite de fondeo).

## Usa / conecta con →
- [[core]]  ·  _module_
- [[core.aunesa]]  ·  _module_
