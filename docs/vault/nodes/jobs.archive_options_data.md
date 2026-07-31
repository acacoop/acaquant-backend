---
id: jobs.archive_options_data
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs\archive_options_data.py
---

# jobs/archive_options_data

> archive_options_data.py — purga de mercado.options_data (SQL).

**Archivo:** `jobs\archive_options_data.py`

## Qué hace
Mantenimiento de `Opciones.Data`: exporta el 100% de la colección a un JSON local (streaming, sin volar memoria) y después purga de Mongo los trades con timestamp anterior a hoy 00:00 ART, dejando solo la rueda en curso. Tiene salvaguarda: aborta el borrado si no exportó al menos el 99% de los docs. Default dry-run; `--apply` ejecuta. Se corre a mano (post-OPEX o para liberar espacio).

Conecta con: lee/borra `Opciones.Data`, escribe el backup a `archive/options_data_*.json` en el repo. Su contraparte de rollup histórico es `jobs.options_rollup`.

## Usa / conecta con →
- [[core.postgres]]  ·  _module_

## Lo usan (backlinks) ←
- [[cron.jobs.archive_options_data]]  ·  _cron_
