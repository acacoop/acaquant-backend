---
id: jobs.dias_habiles
type: module
layer: jobs
repo: backend
tags: [module, jobs, backend]
path: jobs/dias_habiles.py
---

# jobs/dias_habiles

> data_diashabiles.py — Carga días hábiles del calendario argentino a Trading.DiasHabiles.

**Archivo:** `jobs/dias_habiles.py`

## Qué hace
Carga el calendario de días hábiles argentinos del año (excluye fines de semana y feriados vía librería `holidays`) a `Trading.DiasHabiles`. Es la fuente de verdad del calendario hábil que usan otros jobs y motores para contar plazos. Idempotente (upsert por fecha).

Se corre puntualmente (típicamente al inicio del año / cuando hace falta refrescar el calendario).

Conecta con: escribe `Trading.DiasHabiles`. Lo consumen `jobs.cleanup_curvas` y los cálculos de breakevens/anualización que necesitan contar días hábiles.

## Usa / conecta con →
- [[core.mongo]]  ·  _module_

## Lo usan (backlinks) ←
- [[tests.unit.test_dias_habiles]]  ·  _module_
