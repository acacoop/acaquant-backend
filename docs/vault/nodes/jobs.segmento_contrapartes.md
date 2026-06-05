---
id: jobs.segmento_contrapartes
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs\segmento_contrapartes.py
---

# jobs/segmento_contrapartes

> set_segmento_contrapartes.py

**Archivo:** `jobs\segmento_contrapartes.py`

## Qué hace
Script one-shot que agrega/actualiza el campo `segmento` en las contrapartes según reglas simples sobre denominación/contraparte: "FCI" → Fondos, "ALYC" → ALYC, "BANCO" → Bancos. Las que no matchean quedan para asignación manual interactiva por consola (input()).

Conecta con: lee y escribe `CashFlow.Contrapartes` (campo `segmento`). Es utilitario de mantenimiento manual, no un cron (usa input interactivo).

## Usa / conecta con →
- [[core.mongo]]  ·  _module_
- [[db.CashFlow.Contrapartes]]  ·  _collection_
