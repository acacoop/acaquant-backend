# scripts/ — contexto del subdirectorio

One-shot / migraciones / smoke / diagnósticos. El `CLAUDE.md` raíz tiene lo
project-wide.

> **`scripts/` se mantiene MINIMALISTA (REGLA #5).** El 2026-06-06 se borraron
> 76 one-shots ya cumplidos (diag/fix/backfill/seed/rename/cleanup de incidentes
> resueltos). Lo que queda son **herramientas recurrentes**: generadores
> (`gen_*`), perf (`perf_*`, `profile_*`), DBA/monitoreo (`crear_indices`,
> `db_maintenance`, `audit_db`, `diag_atlas_*`, `atlas_health`, `watch_db`,
> `healthcheck_mongo`, `diag_index_usage`, `diag_mongo_connections`),
> seguridad (`security_audit`), feeds/admin (`mae_forex_client`, `partner_user`),
> y los referenciados por skills (index-health, safe-backfill). Segunda purga
> 2026-06-16: −67 one-shots de features ya eliminadas (AuM/Assets, Operaciones/
> NegocioMov/Contrapartes→SQL, gates de migración cumplidos). **Un `diag_*`/`fix_*`/
> `backfill_*` que ya cumplió su función se borra en el mismo commit del fix** — no se acumula.

## REGLA #0 aplicada a scripts

`scripts/` ES el mecanismo de la REGLA #0: **Claude no tiene acceso al
Droplet** → todo lo que tenga que correr en producción se entrega como
archivo acá, se commitea, y el usuario hace `git pull` + `python -m
scripts.<x>` en el Droplet.

- **Diagnóstico one-shot va a un archivo** (`scripts/diag_*.py`), nunca un
  bloque de comandos/queries para copiar y pegar en el chat. Una query
  Mongo de 5 líneas igual va en archivo.
- Una solución por vez, comiteada — cero "probá esto, si no probá esto otro".
- Backfills scopeados/batcheados/idempotentes (REGLA #4): ver `backfill_fci_bruto.py`
  y el skill `safe-backfill`.

## Convención

- `python -m scripts.<cmd>` desde la raíz.
- Diags de solo lectura: usar `core.mongo.get_mongo_client_read()`.
- Los que escriben: `get_mongo_client()`. Nunca `client.close()`.
- Docstring arriba con el propósito + la línea de `Uso:`.
