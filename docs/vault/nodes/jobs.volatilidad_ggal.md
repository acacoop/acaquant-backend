---
id: jobs.volatilidad_ggal
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs/volatilidad_ggal.py
---

# jobs/volatilidad_ggal

**Archivo:** `jobs/volatilidad_ggal.py`

## Qué hace
Job que calcula la volatilidad realizada anualizada (40 ruedas) de GGAL ADR y GGAL local. Baja 65 días de Yahoo, calcula retornos log y la vol con √252, y refresca la colección de detalle más un documento resumen. Insumo para el módulo de opciones GGAL (vol realizada vs implícita).

Conecta con: baja precios de Yahoo (yfinance), escribe `Opciones.VR-GGal` (detalle + resumen) y upsertea `Opciones.Metadata` (type=vr_ggal) que es lo que lee el dashboard de opciones.

## Usa / conecta con →
- [[core]]  ·  _module_
- [[core.job_runs]]  ·  _module_
- [[core.pg_mirror]]  ·  _module_

## Lo usan (backlinks) ←
- [[cron.jobs.volatilidad_ggal]]  ·  _cron_
