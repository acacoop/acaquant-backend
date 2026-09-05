---
name: add-job
description: Agregar un job batch nuevo (cron o one-shot). Cubre el patrón JobRunLogger, la escritura a Postgres, la entrada en crontab vía run_job.sh y los índices en schema.sql.
---

# Agregar un job batch

Se aplica cuando el usuario pide "crear un job que traiga X", "un cron que haga Y" o
similar. El patrón difiere según sea intradía (systemd) o programado (crontab).

> ⚠️ **La base es Postgres/Supabase.** Mongo fue decomisado el 2026-06-29: no existen
> `core/mongo.py`, `get_mongo_client()` ni las colecciones. Un job escrito contra eso
> revienta en el import. Si ves esa forma en un doc viejo, el doc está viejo.

## 1. Decidir la naturaleza del job

- **One-shot manual** (backfill, migración) → solo `python -m jobs.<nombre>` con flags.
  Ejemplo: `jobs/portafolio_backfill.py` (`--diario` / `--desde`).
- **Cron diario/periódico** → entry en `deploy/crontab.txt`. Ejemplo: `jobs/bcra.py`,
  `jobs/ficha_1816.py`.
- **Always-on intradía (WS)** → no es un job, es un **engine**. Va en `engines/` con su
  propio systemd service. No aplica esta skill.

## 2. Escribir el job en `jobs/<nombre>.py`

Patrón estándar (mirar `jobs/ficha_1816.py` o `jobs/tamar_1816.py` como referencia viva):

```python
"""Docstring: qué hace, en qué tabla escribe, su cadencia y cómo se corre."""
from __future__ import annotations

import argparse
import logging

from core.job_runs import JobRunLogger
from core.postgres import get_pool

logger = logging.getLogger(__name__)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry", action="store_true", help="no escribe, solo reporta")
    args = ap.parse_args()

    with JobRunLogger("<nombre_del_job>") as jr:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT ...")
            filas = cur.fetchall()

        jr.set_stat("leidas", len(filas))
        if not filas:
            jr.log("no hay nada que procesar")
            return 0

        if not args.dry:
            escribir(filas)          # ver punto 3
        jr.log(f"{len(filas)} filas {'a escribir' if args.dry else 'escritas'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

**Reglas**:
- **Pool singleton**: `core.postgres.get_pool()`. NO cerrarlo.
- **`python -m jobs.<nombre>` desde la raíz siempre.** `python jobs/x.py` falla —
  `core` no es discoverable.
- Idempotencia: upsert por clave lógica (`ON CONFLICT ... DO UPDATE`), nunca un
  `INSERT` sin dedup. Si corre dos veces, no duplica ni rompe.
- `--dry` para preview sin escribir. Es lo primero que se corre en producción.
- Logs: `jr.log()` (queda en el historial del job Y en stdout) o `logger.info/warning`.

## 3. Cómo escribe

Las escrituras SQL-native van por **`core.pg_mirror`**, que resuelve el upsert:

- `write_native(tabla, key_cols, rows)` — upsert por clave. El caso normal.
- `append_native(tabla, rows)` — insert puro, para series/histórico.
- `replace_native(tabla, rows)` — borra e inserta (catálogos que se rehacen enteros).
- `merge_jsonb_native(tabla, key_cols, key_vals, patch)` — parchea un blob sin pisarlo.
- `prune_native(tabla, col, days)` — retención.

SQL a mano (`cur.execute`) también vale cuando el upsert tiene criterio propio; lo que
NO va es armar el SQL por concatenación de strings — parámetros siempre.

## 4. `JobRunLogger` — recomendado para todo lo que corra por cron

Envolver el trabajo en `with JobRunLogger("nombre") as jr:` persiste la corrida en
**`manager.job_runs`** (inicio, fin, elapsed, ok/error, stats y el log). Es lo que
alimenta el panel `/manager → JOBS` y lo que mira el AV AGENT para saber si un job
dejó de correr.

- `jr.set_stat(clave, valor)` — los números que después se miran (cuántas filas, cuántas
  saltadas y por qué). Un job sin stats no se puede auditar.
- `jr.error(msg)` — marca la corrida como fallida sin cortarla.

Los one-shot manuales no lo necesitan.

## 5. Entrada en `deploy/crontab.txt` (si es cron)

**Todo job pasa por `run_job.sh`**, que agrega LOCK (no se apila si ya corre), TIMEOUT
(lo mata si se cuelga) y el redirect al log. Un cron que invoca `python -m` directo es
exactamente el anti-patrón del incidente de CPU del 2026-06-03.

```cron
# Descripción clara: qué escribe y quién lo consume.
MM HH * * 1-5 /root/TradingAV/deploy/run_job.sh <nombre> <timeout> 'cd /root/TradingAV && /root/TradingAV/venv/bin/python -m jobs.<nombre>'
```

El redirect al log lo hace `run_job.sh` → el comando interno **no** lleva `>> log 2>&1`.

Consideraciones:
- **Fuera de rueda** para todo lo pesado: no 13:00–20:00 UTC L-V, que es cuando corren
  los motores. Post-cierre: ≥ 20:30 UTC.
- `* * 1-5` para L-V; `* * * * *` si corre todos los días.
- **No pisar otros jobs**: si depende de uno que corre 23:00 UTC, arrancar ≥ 23:05.
- Si el job es un backfill o una migración, leer antes la skill `safe-backfill`
  (REGLA #4): scopeado, batcheado, con throttle y medido ANTES de correr.

Aplicar el crontab en el server: `crontab /root/TradingAV/deploy/crontab.txt`.
⚠️ Ese comando **reemplaza el crontab vivo entero** — `deploy/crontab.txt` es la fuente
de verdad, así que tiene que estar completo.

## 6. Schema e índices

Si la tabla destino es nueva, declararla en **`sql/schema.sql`** con su
`CREATE TABLE IF NOT EXISTS` y sus `CREATE INDEX IF NOT EXISTS` (idempotentes). Sin
índice, la query se va a seq scan cuando la tabla crezca. `scripts/apply_schema.py` la
crea en el deploy.

⚠️ Hay un test que **prohíbe declarar en el schema una tabla que no toca nadie**
(`tests/unit/test_schema_sin_tablas_muertas.py`): declarala en el mismo cambio en que
la escribís, no antes.

## 7. Docs y plano

- Sumar al bloque "Jobs críticos diarios" de `CLAUDE.md` si es un job crítico
  (cadencia + qué escribe). Los one-shot manuales no.
- Si tocaste `deploy/crontab.txt` o `deploy/systemd/*.service`, **regenerar el plano en
  el mismo commit**: `python -m scripts.gen_sistema` (y `--check` es bloqueante).

## Criterios de éxito

- ✓ `python -m jobs.<nombre> --dry` corre sin errores y muestra qué haría.
- ✓ `python -m jobs.<nombre>` escribe en Postgres y el segundo run NO duplica.
- ✓ La corrida aparece en `manager.job_runs` con sus stats (si usaste `JobRunLogger`).
- ✓ `ruff check .` y `pytest -ra` en verde.
- ✓ Crontab aplicado, `gen_sistema --check` sincronizado, y el primer run verde
  (`logs/<nombre>.log`).
