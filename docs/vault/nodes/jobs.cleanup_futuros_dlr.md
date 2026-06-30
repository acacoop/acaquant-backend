---
id: jobs.cleanup_futuros_dlr
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs/cleanup_futuros_dlr.py
---

# jobs/cleanup_futuros_dlr

> Limpieza de contratos DLR vencidos en mercado.futuros_dlr_snapshot (SQL).

**Archivo:** `jobs/cleanup_futuros_dlr.py`

## Qué hace
Borra de `Trading.FuturosDLRSnapshot` los contratos DLR (Dólar A3500) ya vencidos. El motor `engines/futuros_dlr.py` hace upsert por ticker y nunca borra, así que al vencer un contrato el doc queda fantasma con su última info: este job lo limpia. Tiene `--dry` que solo lista.

Corre cada mañana antes de que arranque el motor de futuros DLR.

Conecta con: lee/borra de `Trading.FuturosDLRSnapshot`. Complementa a `engines.futuros_dlr` (que escribe esa colección) y sigue la misma cadencia que `jobs.cleanup_curvas`.

## Usa / conecta con →
- [[core.postgres]]  ·  _module_

## Lo usan (backlinks) ←
- [[cron.jobs.cleanup_futuros_dlr]]  ·  _cron_
