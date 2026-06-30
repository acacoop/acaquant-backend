# scripts/ — contexto del subdirectorio

One-shot / migraciones / smoke / diagnósticos. El `CLAUDE.md` raíz tiene lo
project-wide.

> **`scripts/` se mantiene MINIMALISTA (REGLA #5).** Lo que queda son
> **herramientas recurrentes**: generadores (`gen_*`), perf (`perf_*`,
> `profile_*`), DBA/monitoreo SQL, seguridad (`security_audit`), feeds/admin
> (`partner_user`), y los referenciados por skills (index-health,
> safe-backfill). **Un `diag_*`/`fix_*`/`backfill_*` que ya cumplió su función
> se borra en el mismo commit del fix** — no se acumula.

## REGLA #0 aplicada a scripts

`scripts/` ES el mecanismo de la REGLA #0: **Claude no tiene acceso al
Droplet** → todo lo que tenga que correr en producción se entrega como
archivo acá, se commitea, y el usuario hace `git pull` + `python -m
scripts.<x>` en el Droplet.

- **Diagnóstico one-shot va a un archivo** (`scripts/diag_*.py`), nunca un
  bloque de comandos/queries para copiar y pegar en el chat. Una query
  SQL de 5 líneas igual va en archivo.
- Una solución por vez, comiteada — cero "probá esto, si no probá esto otro".
- Backfills scopeados/batcheados/idempotentes (REGLA #4): ver el skill
  `safe-backfill`.

## Convención

- `python -m scripts.<cmd>` desde la raíz.
- Conexión SQL: `core.postgres.get_pool()` — nunca cerrarlo (mata el pool).
- Docstring arriba con el propósito + la línea de `Uso:`.
