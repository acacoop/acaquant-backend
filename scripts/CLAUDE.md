# scripts/ — contexto del subdirectorio

One-shot / migraciones / smoke / diagnósticos. El `CLAUDE.md` raíz tiene lo
project-wide.

> **`scripts/` se mantiene MINIMALISTA (REGLA #5).** Lo que queda son
> **herramientas recurrentes**: generadores (`gen_*`), perf (`perf_*`,
> `profile_*`), DBA/monitoreo SQL, seguridad (`security_audit`), y los
> referenciados por skills (index-health,
> safe-backfill). **Un `diag_*`/`fix_*`/`backfill_*` que ya cumplió su función
> se borra en el mismo commit del fix** — no se acumula.

## ⚠️ QUÉ PUEDE VIVIR ACÁ — la regla, y la congela un test

**Para un script que corre una PERSONA, el repo no puede probar que se usa**: que
nadie lo importe es lo NORMAL. Por eso la carpeta llegó a **143 archivos, 87 de
ellos `diag_*`** — nadie se acuerda de volver a borrar.

Un script vive acá si cumple **al menos una**, y `tests/unit/test_scripts.py`
falla si no:

1. **Algo AUTOMÁTICO lo corre** — crontab, CI, un hook o skill de `.claude/`, o
   un test lo importa.
2. **El CÓDIGO manda a correrlo** — un mensaje de error o docstring que le dice
   al operador `python -m scripts.x`. Si falta, ese mensaje no tiene salida.
3. **ESCRIBE** — altas, cargas, siembras, migraciones, exports, DDL. Eso no es un
   diagnóstico: es la superficie operativa.
4. **Está en `HERRAMIENTAS` de ese test, con un motivo en UNA línea.** Escribir el
   motivo ES el filtro: si no se puede, no hay motivo.

Un `diag_*` read-only que nadie corre **se borra**. Git lo tiene, y un diag se
reescribe en diez minutos con el contexto de HOY — que es mejor que uno de hace
tres meses con el de entonces.

> Hubo un `scripts/diag_scripts_muertos.py` que estimaba esto con heurísticas y
> admitía en su propio docstring que «no puede decidir solo». Se borró: un
> diagnóstico que hay que **acordarse de correr** tiene el mismo problema que el
> que quería resolver.


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
