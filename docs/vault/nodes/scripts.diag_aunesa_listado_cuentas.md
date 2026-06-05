---
id: scripts.diag_aunesa_listado_cuentas
type: module
layer: scripts
repo: backend
tags: [module, scripts, backend]
path: scripts/diag_aunesa_listado_cuentas.py
---

# scripts/diag_aunesa_listado_cuentas

> Diag read-only de Aunesa GET /api/cuentas/listadoCuentas.

**Archivo:** `scripts/diag_aunesa_listado_cuentas.py`

## Qué hace
Diagnóstico read-only del endpoint de Aunesa /cuentas/listadoCuentas. Trae el documento COMPLETO de cada comitente para descubrir el shape real (valores de tipo/estado, campos de segmentación como cartera/categoria/clase, y administrador.operador.email que mapea operador↔usuario). Fue el insumo para diseñar el master Clientes.Comitentes del Tablero Comercial. No escribe nada. Se corre con python -m scripts.diag_aunesa_listado_cuentas [--tipo Comitente] [--cuenta 805].
Conecta con: API Aunesa (listadoCuentas), config; el mismo endpoint que usa jobs.aum (obtener_cuentas). Insumo de Clientes.Comitentes y docs/TABLERO_COMERCIAL.md.

## Usa / conecta con →
- [[config]]  ·  _module_
