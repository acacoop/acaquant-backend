---
id: scripts.diag_aunesa_informes
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_aunesa_informes.py
---

# scripts/diag_aunesa_informes

> Diag read-only de Aunesa GET /api/operaciones/informes ("Informe de operaciones").

**Archivo:** `scripts/diag_aunesa_informes.py`

## Qué hace
Diagnóstico read-only del endpoint de Aunesa /operaciones/informes, que es boleto-level y SÍ trae el desglose de aranceles/gastos/impuestos que consolidadosGenerales no tiene. Dumpea la unión de claves y los items con foco en los campos de costo, para evaluar integrarlo y capturar aranceles. Consulta por cuenta (con fechaDesde/fechaHasta obligatorias) o por boleto. No escribe en Mongo. Se corre con python -m scripts.diag_aunesa_informes --cuenta 805 [--desde --hasta] | --boleto BOLxxx.
Conecta con: API Aunesa (informes), config. Exploratorio; fue el insumo para api.services.aunesa_informes / aunesa_aranceles.

## Usa / conecta con →
- [[config]]  ·  _module_
