# scripts/ — contexto del subdirectorio

One-shot / migraciones / smoke / diagnósticos. El `CLAUDE.md` raíz tiene lo
project-wide.

## REGLA #0 aplicada a scripts

`scripts/` ES el mecanismo de la REGLA #0: **Claude no tiene acceso al
Droplet** → todo lo que tenga que correr en producción se entrega como
archivo acá, se commitea, y el usuario hace `git pull` + `python -m
scripts.<x>` en el Droplet.

- **Diagnóstico one-shot va a un archivo** (`scripts/diag_*.py`), nunca un
  bloque de comandos/queries para copiar y pegar en el chat. Una query
  Mongo de 5 líneas igual va en archivo.
- Una solución por vez, comiteada — cero "probá esto, si no probá esto otro".
- Patrón backfill multi-mes: ver `backfill_2026.py` / `retry_cuentas_2026.py`
  (por mes: DELETE → BACKFILL → FIX, con `RESUMEN` al final) y el skill
  `backfill-mes`.

## Convención

- `python -m scripts.<cmd>` desde la raíz.
- Diags de solo lectura: usar `core.mongo.get_mongo_client_read()`.
- Los que escriben: `get_mongo_client()`. Nunca `client.close()`.
- Docstring arriba con el propósito + la línea de `Uso:`.
