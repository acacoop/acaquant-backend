---
name: audit-obsoleto
description: Auditar el repo para detectar código y archivos obsoletos — scripts one-shot ya corridos, tests de código removido, docs viejos, JSONs/assets sin uso. Devuelve listas BORRAR / REVISAR / MANTENER. Invocar on-demand (mensual, o cuando el repo se sienta cargado), NO en cada sesión.
---

# Auditoría de código obsoleto

Playbook para una pasada de limpieza. **On-demand** — no es per-sesión
(escanear todo el repo en cada arranque es caro al pedo).

## Alcance
- `scripts/*.py` — one-shot ya corridos vs tooling reusable vs superseded.
- `tests/**` — tests que importan código ya removido.
- `docs/` — `precios_*.json` consumidos, `.xlsx`/`.png` sueltos, `wip_*.md` cerrados.
- Working tree — archivos untracked que son basura (capturas, PDFs, scratch).

## Clasificar cada SCRIPT
Leer el docstring (primeras ~25 líneas) y poner en una de:
- **TOOLING REUSABLE** — se re-corre (diags genéricos, `perf_scan`, fixes
  parametrizables, seeds idempotentes). → MANTENER.
- **ONE-SHOT YA CORRIDO** — migración/backfill/seed/cleanup puntual ya
  ejecutado, no se vuelve a correr. → candidato a BORRAR.
- **OBSOLETO / SUPERSEDED** — apunta a funcionalidad REMOVIDA del repo. → BORRAR.

Para cada TEST: grepear su import principal contra el código real. Si el
módulo/función ya no existe, el test está muerto.

## Verificaciones de referencia (ANTES de marcar algo como obsoleto)
Comprobar que NO esté referenciado en:
- `deploy/crontab.txt`
- `.github/workflows/ci.yml`
- otros scripts (los `backfill_*.py` / `retry_*.py` invocan otros vía `python -m`)
- `.claude/` (skills, commands, agents, hooks)
- imports desde `jobs/`, `engines/`, `api/`

Leer también los `docs/wip_*.md` — listan tooling que SIGUE vigente para
trabajo en curso (esos no son obsoletos aunque parezcan one-shot).

## Cómo correrlo
Conviene **delegar el escaneo en un subagent** (`general-purpose`): es
lectura de muchos archivos y se beneficia del contexto limpio. El subagent
devuelve el informe; el thread principal lo sintetiza.

## Entregable
- Tabla de scripts: nombre · categoría · razón (1 línea) · ¿referenciado? (dónde).
- Tabla de tests obsoletos (solo los que testean código removido).
- Veredicto sobre los archivos de `docs/`.
- Listas finales separadas: **BORRAR SEGURO** / **REVISAR CON EL USUARIO**
  (dudosos) / **MANTENER**.

**Nunca borrar nada sin confirmación del usuario.** El skill produce el
informe; el usuario decide.
