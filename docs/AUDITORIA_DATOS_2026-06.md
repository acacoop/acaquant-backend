# Auditoría de datos, queries, jobs y observabilidad — 2026-06-03

Auditoría completa disparada tras el incidente de CPU 100% en el cluster Atlas
(M10). Hecha por 5 auditores en paralelo, **fundada en código real** (file:line).
Lo cuantitativo de prod (tamaños, planes de query) está marcado como *estimado /
a medir* — REGLA #2: hay que correr los diags antes de tocar.

> **Nota de método.** Varios hallazgos afirman "COLLSCAN" a partir del diseño de
> la query vs los índices declarados, NO de un `explain()` medido. Antes de crear
> índices o reescribir, correr los diags read-only (ver §7).

---

## 0. Resumen ejecutivo

**Qué pasó hoy, y por qué no fue mala suerte:** dos jobs que escriben en
`CashFlow.Operaciones` (`descubrir_cuentas`, colgado 5h, y `fci_bilateral`, con
una query COLLSCAN ya diagnosticada y nunca arreglada) corrieron en simultáneo,
en horario de mercado, sobre un cluster chico. **No existe NINGÚN mecanismo de
liveness** (ni lock, ni timeout, ni watchdog) para ningún job — verificado. Así
que un job lento no solo tarda: se clona en cada tick del cron y multiplica la
carga hasta tirar el CPU. Y como solo ~20% de los jobs registran su corrida,
nadie se enteró hasta la alerta genérica de Atlas.

**Las 5 causas sistémicas (cada una su sección):**

| # | Tema | Estado | Lo más grave |
|---|---|---|---|
| 1 | Queries e índices | 🔴 | COLLSCANs en `comercial.py`; regex sobre `cuenta` en vez de `id_cuenta` indexado (en TODO el scope de grupos) |
| 2 | Jobs, crons y liveness | 🔴 | Cero locks/timeouts/watchdog → la causa raíz de hoy. `fci_bilateral` con query lenta sin arreglar |
| 3 | Observabilidad y alerting | 🔴 | Solo 6/30 jobs observados; alerta solo *post-mortem*, nunca *in-flight*; cero monitoreo de CPU/DB |
| 4 | Arquitectura de datos | 🟡 | `/ops/serie\|aranceles\|agro` re-agregan TODA la historia inmutable en cada request → faltan rollups |
| 5 | Calidad de código | 🟡 | `api/agent/` legacy SIGUE montado (riesgo import-chain); lógica de negocio en routers; `except: pass` en enriquecimiento de AuM |

**La buena noticia:** la solución NO es un stack nuevo. El patrón de pre-cálculo
ya existe (`PnLTotalesCache`, `ConsolidadoCuentas`), `TimeSales` ya es time-series
nativa, los snapshots son upsert (no crecen), y la infra de alerting (`JobRunLogger`,
Telegram, `/manager/status`, Atlas Admin API) ya está — falta **cablearla**.

---

## 1. Queries e índices

### 🔴 Críticos
- **C1 — `informe_comercial` / `debug_comercial`** (`api/services/comercial.py:713-727`, `:839-849`): `$or`(categoria | `arancel>0`) + `$nin` sobre `unidad`, **sin fecha ni cuenta** → COLLSCAN de NegocioMovimientos (~337k) en cada apertura del INFORME (TTL 600s). → precompute `Clientes.ComercialCache` (ya previsto en el docstring) o flag materializado + índice.
- **C2 — `serie_comercial` / `_volumen_total` vista TODOS** (`comercial.py:347-354`, `:186-191`, match en `:162-178`): con `__todos__` no hay cuenta ni fecha; `categoria $in` + `unidad $nin` no encaja en ningún índice → COLLSCAN+group de toda la colección. → servir desde `Clientes.ActividadMensual`.
- **C3 — `resumen_por_operador`** (`comercial.py:599-610`): `distinct("cuenta")` sobre Operaciones (~487k) sin índice prefijo → scan. → `$group` cubierto por `cuenta_concertacion`, o derivar del aggregate ya hecho (el `distinct` es redundante).
- **C4 — `analisis_comercial` / `_aranceles_por_cuenta(None)`** (`comercial.py:464-467`, `:206-219`): triple anti-patrón — `arancel $gt 0` (sin índice) + `tipo_operacion $not /Cierre/` (regex negada inindexable) + `etapa $ne`. Sin cuenta ni fecha. → usar `es_cierre: False` (campo YA materializado, igual que `_ops_match` del router) + índice/precompute.
- **C5 — Scope de grupos por regex sobre `cuenta`** (`api/services/_grupos_scope.py:101-102`): inyecta `{"cuenta": {"$regex": "^\\[(101|102|...)\\]"}}` en TODOS los pipelines de operaciones/negocio scopeados. `cuenta` no tiene índice usable; el regex se evalúa por doc. **`id_cuenta` está denormalizado e indexado** → cambiar a `{"id_cuenta": {"$in": scope}}` (igualdad, indexable). *(Es el mismo bug que arreglamos hoy en `valuaciones.py`, pero acá afecta a toda la vista de operaciones.)*
- **C6 — `_load_pnl_bulk_deps`** (`api/services/pnl.py:881-895`): scan de NegocioMovimientos con `ticker $ne None` sin fecha + regex en Python por doc para extraer `id_cuenta` que **ya está denormalizado**. Mitigado por ser job de precompute. → agrupar por `id_cuenta` existente.

### 🟡 Medios
- **M1** `listar_flujo` (`operaciones.py:83-86`): `$not /Futuros|Opciones|colocadora/` (regex negada) + `$in` de cientos de cuentas + sort sin cobertura → materializar flags.
- **M2** `_aranceles_por_cuenta`/`informe_segmento_detalle` (`comercial.py:199,207,978-981`): mismo `$not /Cierre/` + `$gt`/`$ne` → `es_cierre: False`.
- **M3** `stats`/`ops_fechas`/`ops_cuentas_list`/`negocio_fechas`: `$group`/`distinct` sobre toda la colección → subir TTL o precomputar lista de cuentas; fechas quedan cubiertas por índice (verificar DISTINCT_SCAN).
- **M4** `ops_mercados`/`ops_segmentos` (`operaciones.py:861,1190`): `distinct` sobre campos sin índice prefijo → índices `{mercado:1}`/`{segmento:1}` o TTL alto (casi estáticos).
- **M5** `movimientos_mes` (`valuaciones.py:1414-1418`): regex sobre `cuenta` + `fecha $regex` → `id_cuenta` + range de fecha (inconsistente con el resto del módulo que ya migró).
- **M6** `_cuentas_filter` cooperativas/sin_accionistas (`_cuentas_filter.py:69-78`): `$nin` + regex sin ancla `\bcoop` → materializar flag `es_cooperativa`/`es_accionista`.

### Índices — resumen
- **Operaciones**: `concertacion` (simple) es **redundante** (prefijo de 4 compuestos `concertacion_*`) → candidato a drop. Sin cobertura: `arancel`, `etapa`, `mercado`/`segmento` standalone, `cuenta` standalone.
- **NegocioMovimientos**: ningún índice empieza por `categoria` → C1/C2 sin cobertura. `$nin unidad` nunca indexa.
- **Medir con `$indexStats`** (vía `scripts/audit_db`) qué índices están muertos antes de dropear (REGLA #2).

---

## 2. Jobs, crons y liveness

**Hallazgo estructural (causa raíz de hoy):** **no existe ningún** flock, pidfile,
`timeout`, `RuntimeMaxSec` ni watchdog para los crons. Si un job se cuelga, corre
indefinido y el siguiente tick lanza otra instancia encima.

### 🔴 Críticos
- **J1 — `fci_bilateral`** (`jobs/fci_bilateral.py:136-143`): `$or` + `$regex "^CL" "i"` sobre NegocioMovimientos sin índice de respaldo → COLLSCAN; re-procesa TODO el histórico cada hora (debería leer solo últimos N días). → índice `categoria`(+`comprobante`); reemplazar regex `i` por campo derivado `es_cl` o range `{$gte:"CL",$lt:"CM"}`; acotar por fecha. Correr `scripts/diag_fci_job_perf.py` primero.
- **J2 — `descubrir_cuentas`** (`jobs/descubrir_cuentas.py:165-185`): loop 1..12000 × 2 llamadas pyRofex **sin timeout de socket** ni cota de tiempo total. 12000×0.2s sleep = 40min solo de sleeps. Hoy corrió 5h. → timeout de proceso + lock + cota de tiempo en el loop + reducir el rango (cuentas conocidas + barrido incremental).
- **J3 — Cero protección contra doble ejecución** en TODOS los jobs. Crítico en los frecuentes: `pnl_totales_precompute` (cada 30min, `delete_many({})`+`insert_many`), `aum` (`delete`+`bulk_write` por cuenta), `market_quotes` (cada 1min). → lock por job (flock).
- **J4 — `pnl_totales_precompute`** (`jobs/pnl_totales_precompute.py:33-38`): `delete_many({})`+`insert_many` no atómico → la vista `/pnl-todas` lee colección **vacía** durante el swap. Sin `JobRunLogger` → no alerta si falla. → swap atómico (temp + rename, o upsert+borrar faltantes) + `JobRunLogger`.
- **J5 — `consolidado_cuentas`** (`jobs/consolidado_cuentas.py:31-32`): mismo patrón, diario, sin alerta.

### 🟡 Importantes
- **J6** `aum`: `delete`+`bulk_write` con gap, sin `JobRunLogger`, 5×/día.
- **J7** Cadenas `A && B && C` (`crontab.txt:90,76,80,142-146,183`): si B cuelga, C nunca corre y el cron queda pegado; el tick siguiente lanza otra cadena. Si `cashflow`≠0, `sync_api_copies` no corre → copias `*API` desincronizadas en silencio.
- **J8** `market_quotes` cada 1min + yfinance **sin timeout** → pila de procesos colgados (el de mayor frecuencia).
- **J9** **Solo 6 jobs usan `JobRunLogger`** (`argentina_datos, actividad_mensual, aranceles, fci_bilateral, operaciones_informes, sync_comitentes`). NO lo usan los críticos: `aum, pnl_totales_precompute, consolidado_cuentas, bcra, snapshot_cierre, fair_value, negocio_movimientos, descubrir_cuentas` → fallan en silencio.
- **J10-J12** `operaciones_informes` (distinct+ThreadPool, ya pausado), `flujo_contrapartes`/`cashflow` (requests sin retry), `comercial_warm` (cada 4min pega al uvicorn local → bucle de realimentación si la API está lenta).

### Diseño de la solución (lo que falta)
1. **`deploy/run_job.sh`**: wrapper con `flock -n` (no-doble-ejecución) + `timeout <Nm>` (mata cuelgues) + tee al log. Reescribir `crontab.txt` para invocarlo. **Una pieza tapa J2, J3, J7, J8, J12.** Hoy hubiera cortado ambos cuelgues.
2. **`JobRunLogger.__enter__` escribe doc `running` al arrancar** (hoy solo al salir → un job colgado no deja rastro). Envolver los críticos (J9).
3. **`fci_bilateral`** índice + ventana temporal (J1).
4. **`scripts/watchdog_jobs.py`** cada 5min: detecta docs `running` con `elapsed > presupuesto` → alerta/mata (ver §3).

---

## 3. Observabilidad, alerting y evaluación automática

### Qué existe
- **`JobRunLogger`** (`core/job_runs.py`) → `Manager.JobRuns`, consultado por `/api/manager/jobs/history`. Cobertura ~20% (J9).
- **Telegram** (`core/notify.py`, bot @acaquantbot): alerta SOLO cuando un job que usa JobRunLogger **termina** mal, o cuando un motor WS agota reconexión. **Nunca alerta in-flight, ni CPU, ni datos stale.**
- **`/manager/status`** (`api/routers/manager/status.py`): YA evalúa frescura de 13 motores + 5 jobs + 7 APIs con umbrales y ventana de rueda — **pero es pull-only y muere en el browser** (solo se evalúa si un manager tiene la pestaña abierta).
- **`/api/health`**: devuelve `{ok}` estático, **no chequea Mongo**.
- **systemd**: `Restart=always` recupera *crashes*, NO *zombies* (proceso vivo, WS muerto). Los motores NO tienen `RuntimeMaxSec` (la API sí, 8h).
- **El user de Mongo NO tiene `inprog`/`currentOp`** (confirmado en `diag_db_load.py:55-61`) → monitoreo de DB debe ir por **Atlas Admin API** (que `deploy/atlas_cluster.sh` ya usa).

### Lo que falta (prioridad P0)
- **B6 — Watchdog de procesos**: `JobRunLogger` escribe `running` al arrancar (prereq) + `jobs/watchdog.py` cada 5min alerta si `elapsed > presupuesto`. **Esta regla te avisaba HOY a los 60min en vez de a las 5h.** + `timeout` defensivo en crontab.
- **B7 — Monitor de salud de la DB**: `jobs/watchdog.py` lee CPU del cluster vía **Atlas Admin API** (`core/atlas_api.py` nuevo, reusa `ATLAS_*` keys) → alerta CPU>80% sostenido. Correlacionado con B6 da causa raíz automática: *"🔴 CPU 95% + job X corriendo hace 4h"*.
- **B8 — Frescura activa**: extraer el core de `status.py` a un service puro y que `watchdog.py` lo evalúe y alerte (cubre el zombie de motores). Bonus: cambiar `systemctl start motor_*` por `restart` en el crontab.
- **B9 — Panel "Salud del Sistema"** en `/manager` (P2): status + jobs running ahora + CPU cluster + últimas alertas.

### "Conectar un agente que evalúe esto" — la respuesta honesta
Lo que describís ("algo que evalúe solo, procesos que corren cuando no deberían")
**no es un LLM** — es un **watchdog rule-based**: `jobs/watchdog.py`, cron cada
5min, evalúa reglas deterministas sobre datos que ya tenés, alerta por el
`send_telegram()` que ya existe. Más confiable, barato y debuggeable que un agente
LLM para esto. **Ese ES el "agente" que pedís.** (Un LLM tendría sentido después,
solo para redactar el resumen correlacionado de la alerta — no para detectar.)

---

## 4. Arquitectura de datos y agregaciones

### Lo que está BIEN (no tocar)
- `MarketSnapshot`/`CedearsSnapshot` son **upsert por ticker** (no crecen) → NO migrar a time-series.
- `TimeSales` (único append) **ya es time-series nativa con TTL 90d** (`scripts/db_maintenance.py`). Verificar que el TTL esté aplicado en prod (`--apply`).
- El patrón de pre-cálculo ya existe y funciona (`PnLTotalesCache`, `ConsolidadoCuentas`).

### 🟡 Lo que falta
- **A1/A2 — Rollup diario de Operaciones (LA propuesta central)**: `ops_serie`/`ops_aranceles`/`ops_agro` (`operaciones.py:898,1101,984`) re-agregan TODA la historia inmutable en cada request (~180-300k docs, ~1-1.5s medido en `diag_perf_aranceles`). → colección `CashFlow.OperacionesRollupDiario`, grano `{fecha, moneda, mercado, operacion, segmento, nivel_3, commodity}` con `{bruto, arancel, toneladas, n}`. Mantenimiento: recompute de últimos N≈5 días en cada corrida (boletos que llegan tarde) + full rebuild on-demand. Las tablas por-cuenta (alta cardinalidad) siguen leyendo Operaciones raw **acotadas a `[desde,hasta]`** (ya son chicas). Baja el primer request de ~1.5s a <100ms y deja de competir con los motores.
- **A3 — Live fallback en Operaciones**: el rollup sirve `fecha<hoy`; hoy se agrega en vivo (un día, barato). Mismo patrón que `analitica/renta_fija/carry_trade`.
- **A4 — `comercial.py` sin precompute**: ver C1-C4 → `Clientes.ComercialCache` (1 doc por `id_cuenta`).
- **A5 — Separación de cargas**: los crons de precompute (`pnl_totales_precompute`, `consolidado_cuentas`) leen del **primario** (compiten con motores). → leer del secundario / agendar fuera de rueda. (Nodo de analytics dedicado recién en M30+; agotar rollups antes de pagar tier.)
- **A7 — Right-sizing**: medir `idx_size` + `$indexStats` con `scripts/audit_db` antes de decidir tier. Candidatos a índice muerto: los 4 `concertacion_*` de Operaciones.

---

## 5. Calidad de código

Cumple: `@cached` con kwargs (todos los call-sites ✓), nunca `client.close()` sobre singletons ✓, `core/` no importa del proyecto ✓. `from api.main import app` importa limpio (223 routes).

### 🔴 Alta
- **Q1 — `api/agent/` legacy SIGUE montado** (`api/main.py:239` → `chat.py:30-36` imports top-level): la doc lo marca "no en uso" pero `chat.router` está montado y un import error en cualquier módulo legacy tumba TODA la API. → desmontar `chat.router` o hacer imports lazy; decidir explícito (hoy doc y código se contradicen).
- **Q2 — Lógica de negocio en routers** (`operaciones.py` ~1200 líneas, `negocio_cuentas`, `ops_*`, etc.): viola la regla de capas; no testeable sin FastAPI ni reusable desde MCP. → mover pipelines a `api/services/`.

### 🟡 Media
- **Q3** `_grupos_scope.py:24` importa FastAPI (service que debe ser puro) → mover Depends/HTTPException a router/deps.
- **Q4** `except Exception: pass` que silencia degradación en `portfolio.py:353-354` (enriquecimiento TEA/paridad del AuM, sin log) y `analitica.py:79,138,208` → al menos `logger.warning(exc_info=True)`.
- **Q5** `core/grupos.py:37` (+`roles.py:33`) usan cliente RW en hot-path de lectura de cada request → usar read client.
- **Q6** `detail=str(e)` en ~25 sitios filtra internals en 500s → exception_handler global genérico + log.
- **Q7** N+1 `find_one` en loop (`manager/checks.py:71,118`, `precios_acciones_daily.py:79`).
- **Q8** `count_documents({})` (`operaciones_informes.py:368`, `archive_options_data.py`) → `estimated_document_count()`.

### 🟢 Baja
- **Q9** validación moneda+fechas duplicada 5× en `operaciones.py` → helper.
- **Q10** live-fallback reimplementado en 4 services → helper `_cierre.py`.
- **Q11** funciones `@cached` que aceptan posicional → agregar `*,` (keyword-only) para que la regla sea imposible de violar.
- **Q12** error dict cacheado en `comparar_inversion.py:358`.

---

## 6. Roadmap priorizado

### P0 — Prevenir que el incidente de hoy se repita (esta semana)
1. **`deploy/run_job.sh`** (flock + timeout) + reescribir `crontab.txt`. → J2, J3, J7, J8, J12.
2. **`fci_bilateral`**: índice + ventana de fecha + quitar regex `i`. → J1. (correr `diag_fci_job_perf` primero)
3. **`JobRunLogger.__enter__` escribe `running`** + envolver jobs críticos. → J4, J5, J9.
4. **`jobs/watchdog.py`** (cron 5min): job running > presupuesto + CPU Atlas > 80% → Telegram. → B6, B7, B10. **= "el agente que evalúa solo".**
5. **C5/M5**: scope y `movimientos_mes` de regex-`cuenta` → `id_cuenta`. (mismo fix de hoy, replicado)

### P1 — Performance estructural (próximas 2-3 semanas)
6. **Rollup diario de Operaciones** (`OperacionesRollupDiario`) + migrar `ops_serie/aranceles/agro` + live fallback. → A1, A2, A3.
7. **`Clientes.ComercialCache`** + arreglar COLLSCANs de `comercial.py`. → C1-C4, A4.
8. **Swap atómico** en `pnl_totales_precompute`/`consolidado_cuentas`. → J4, J5.
9. **Frescura activa** + `restart` en cron de motores. → B8.

### P2 — Limpieza y profundidad (cuando baje la espuma)
10. Decidir `api/agent/` legacy (desmontar o sacar rótulo). → Q1.
11. Drop de índices muertos (medir con `audit_db` primero). → A7.
12. Panel "Salud del Sistema". → B9.
13. Refactors de capas, helpers, `detail=str(e)`, read-client en `grupos/roles`. → Q2-Q12.

---

## 7. Medir ANTES de codear (REGLA #2)

Diags read-only a correr en el Droplet y traer los números:
- `python -m scripts.audit_db` → tamaños, `idx_size`, `$indexStats` (índices muertos), #docs por colección.
- `python -m scripts.diag_perf_aranceles` → plan real de la serie de aranceles.
- `python -m scripts.diag_fci_job_perf` → plan del `$or`+regex de fci_bilateral.
- `explain(executionStats)` sobre `informe_comercial` (C1) y `_aranceles_por_cuenta(None)` (C4) → confirmar COLLSCAN antes de elegir índice vs precompute.
- Atlas: confirmar TTL de `TimeSales` aplicado; medir `wiredTiger.cache` para el right-sizing.
