---
id: scripts.diag_aunesa_consolidados
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_aunesa_consolidados.py
---

# scripts/diag_aunesa_consolidados

> Diag read-only de Aunesa GET /operaciones/consolidadosGenerales.

**Archivo:** `scripts/diag_aunesa_consolidados.py`

## Qué hace
Diagnóstico read-only que pega directo al endpoint de Aunesa /operaciones/consolidadosGenerales (el que alimenta NegocioMovimientos) y dumpea TODOS los campos crudos del boleto, sin parsear ni descartar nada. Se usó para chequear si ese endpoint trae aranceles/comisiones (no los trae — por eso se evaluó el de informes). Filtra client-side por id de cuenta sobre un rango de fechas. No escribe en Mongo. Se corre con python -m scripts.diag_aunesa_consolidados --cuenta 805 [--desde --hasta].
Conecta con: API Aunesa (login + consolidadosGenerales), config (credenciales AUNESA_*). One-shot exploratorio, complementa el flujo de aunesa_negocio.

## Usa / conecta con →
- [[config]]  ·  _module_
