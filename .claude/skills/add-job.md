---
name: add-job
description: Agregar un job batch nuevo (cron o one-shot). Cubre el patrón JobRunLogger, entry en crontab, encadenamiento con sync API, systemd vs python -m, y naming de colección destino.
---

# Agregar un job batch

Se aplica cuando el usuario pide "crear un job que traiga X", "un cron que haga Y" o similar. El patrón difiere según sea intradía (systemd) o programado (crontab).

## 1. Decidir la naturaleza del job

- **One-shot manual** (backfill, migración) → solo `python -m jobs.<nombre>` con flags. Ejemplo: `jobs/aum_backfill.py`.
- **Cron diario/periódico** → entry en `deploy/crontab.txt`. Ejemplo: `jobs/bcra.py`, `jobs/argentina_datos.py`.
- **Always-on intradía (WS)** → no es un job, es un **engine**. Va en `engines/` con su propio systemd service. No aplica esta skill.

## 2. Escribir el job en `jobs/<nombre>.py`

Patrón estándar:

```python
"""Docstring: qué hace, escribe qué, cadencia, uso."""
from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime

from core.mongo import get_mongo_client
from core.job_runs import JobRunLogger  # si querés que aparezca en Manager.JobRuns

logger = logging.getLogger(__name__)


def run(args) -> dict:
    client = get_mongo_client()
    col = client["<DB>"]["<Collection>"]
    # ... lógica ...
    return {"ok": True, "inserted": n}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--today", action="store_true")
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()

    with JobRunLogger("nombre_del_job"):  # logea inicio/fin/elapsed en Manager.JobRuns
        result = run(args)
    logger.info("listo: %s", result)


if __name__ == "__main__":
    main()
```

**Reglas**:
- Siempre `get_mongo_client()` (rw, no ro) — los jobs escriben.
- Idempotencia: upserts con clave lógica, no `insert_many` sin dedup. Si corre dos veces, no debería duplicar ni romper.
- `--dry` flag para preview sin tocar Mongo.
- `--today` flag si se usa en cron (suele filtrar al día en curso).
- Logs a `logger.info/warning/error` — el cron captura stdout/stderr.

## 3. `JobRunLogger` (opcional, recomendado para crons)

Si querés que el job aparezca en el panel `/manager → JOBS` con su historial (éxito/fail/elapsed), envolver el work en `with JobRunLogger("nombre"):`. Persiste en `Manager.JobRuns` con `{nombre, started_at, finished_at, elapsed_s, ok, error?, output?}`.

Jobs one-shot manuales no lo necesitan (no están en cron).

## 4. Entrada en `deploy/crontab.txt` (si es cron)

Formato:

```cron
# Descripción clara (qué escribe, qué consume)
MM HH * * D-V cd /root/TradingAV && /root/TradingAV/venv/bin/python -m jobs.<nombre> [flags] >> /root/TradingAV/logs/<nombre>.log 2>&1
```

Consideraciones:
- **Timing**: evitar la ventana Atlas pausada (04:00–11:20 UTC). Para jobs post-cierre usar ≥ 22:00 UTC.
- **Mercado**: `* * 1-5` para L-V. Si corre todos los días: `* * * * *`.
- **No pisar otros jobs**: si el job necesita data de otro job que corre a las 23:00 UTC, arrancar ≥ 23:05 UTC.
- Aplicar el crontab en server: `crontab /root/TradingAV/deploy/crontab.txt`.

## 5. Sync API (si escribe colecciones originales)

Si tu job escribe a una de las colecciones que tienen copia derivada `*API.*API` (ver `docs/API_MIGRATIONS.md`), **encadenar el sync post-job** en el crontab:

```cron
0 23 * * 1-5 cd /root/TradingAV && /root/TradingAV/venv/bin/python -m jobs.aum >> .../aum.log 2>&1 && /root/TradingAV/venv/bin/python -m jobs.sync_api_copies --aum --titulos >> .../sync_api.log 2>&1
```

Flags de `sync_api_copies`: `--carteras`, `--aum`, `--titulos`, `--flujo`, `--movimientos`, `--all`. El `&&` garantiza que si el job fuente falla, el sync no corre (no ensucia data).

## 6. Índices

Si la colección destino es nueva, agregar el índice en `scripts/crear_indices.py` (idempotente). Sin índice, las queries se van a aggregate scan completo cuando la colección crezca.

## 7. Docs

Sumar al bloque "Jobs batch" de `CLAUDE.md` si es un job crítico (cadencia + qué escribe). Los one-shot manuales no — quedan implícitos.

## Criterios de éxito

- ✓ `python -m jobs.<nombre> --dry` corre sin errores y muestra qué haría.
- ✓ `python -m jobs.<nombre>` escribe a Mongo y el segundo run no duplica.
- ✓ Aparece en `Manager.JobRuns` si usaste `JobRunLogger`.
- ✓ Crontab aplicado y primer run fue verde (revisar `logs/<nombre>.log`).
- ✓ Si corresponde, `jobs.sync_api_copies` corrió después y la colección API tiene la data nueva.
