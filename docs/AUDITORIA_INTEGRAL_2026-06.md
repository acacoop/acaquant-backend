# Auditoría Integral TradingAV — 2026-06-05

> Consolidación de **8 auditorías read-only** (código muerto, scripts, docs,
> Mongo/índices, performance, arquitectura, seguridad/ops, frontend Next.js).
> Deduplicada, priorizada P0→P3, con esfuerzo (S/M/L), riesgo y quick wins.
> **Cada ítem lleva `path:linea`.** Lo que requiere medir en prod antes de
> tocar está marcado explícito (REGLA #2) en la sección final.

---

## 1. Resumen ejecutivo

El sistema tiene **base sólida** (reglas REGLA #0-4, RBAC multicapa, rollups
ya implementados, watchdog deployado, run_job.sh con locks/timeouts). Las
auditorías **NO** encontraron necesidad de stack nuevo ni contradicciones
mayores de datos. Lo que hay es **deuda operativa y de mantenibilidad** con
unos pocos focos de riesgo real de prod.

**Hallazgos por severidad (post-dedup):**

| Sev | Cantidad | Temática dominante |
|---|---|---|
| **P0** | 0 (ninguno nuevo) | El incidente boleto/TTL ya tiene fix en repo, falta **aplicar** |
| **P1** | 11 | Índice boleto sin aplicar, TTL OrdenesAudit, jobs sin JobRunLogger, swaps no-atómicos, violaciones de capas, duplicación de constantes, regex sobre `cuenta`, ComercialCache pendiente, MCP OAuth state, megafiles frontend, política de archivado |
| **P2** | ~28 | Índices redundantes, `find({})` sin proyección, `except: pass` sin log, módulos gigantes, rate-limits faltantes, timeouts systemd, docs desactualizadas, inconsistencias de error-handling en frontend |
| **P3** | ~17 | Type hints, INDEX.md de docs, validaciones de rango, nomenclatura, memoización frontend |

**Los 3 temas más importantes:**

1. **Cerrar el incidente recurrente de CPU 100% del M10** — los fixes ya están
   en el repo (`fix_indice_boleto.py`, `fix_ordenes_audit_ttl.py`) pero **no se
   aplicaron**. Es la causa raíz #1 de las caídas (REGLA #4). Requiere medir +
   correr fuera de rueda.
2. **Observabilidad de jobs críticos** — `aum`, `pnl_totales_precompute`,
   `consolidado_cuentas`, `bcra`, `cashflow`, `snapshot_cierre`,
   `negocio_movimientos` corren sin `JobRunLogger` → fallos silenciosos, el
   watchdog no los ve. Además `delete_many+insert_many` no-atómico deja ventanas
   de colección vacía en intraday.
3. **Aislamiento de capas + fuente única de verdad** — `jobs/` y `engines/`
   importan de `api/services/` (viola la regla de capas) y hay constantes
   críticas duplicadas (`_TIPOS_DIVISOR_100`) → desincronización silenciosa.
   Sumado: 3 megafiles backend (operaciones.py, comercial.py, valuaciones.py)
   y 2 god-components frontend (manager-view.tsx 3422 líneas).

---

## 2. Quick wins (alto impacto / bajo esfuerzo / bajo riesgo)

Orden recomendado de ejecución. Todos S, riesgo bajo, ganancia inmediata.

| # | Quick win | Path:línea | Esfuerzo | Por qué |
|---|---|---|---|---|
| QW1 | Envolver jobs críticos en `with JobRunLogger("nombre"):` | jobs/aum.py, jobs/pnl_totales_precompute.py, jobs/consolidado_cuentas.py, jobs/cashflow.py, jobs/bcra.py | S | Observabilidad inmediata + alertas Telegram en 3 líneas/job. Hoy fallan en silencio. |
| QW2 | `except Exception: pass` → `logger.warning(..., exc_info=True)` en enriquecimiento AuM | api/services/portfolio.py:353 | S | Degradación de AuM (falta TEA) hoy es invisible. |
| QW3 | Remover variable muerta `needed` (F841) | api/services/scanner.py:367 | S | Ruff la marca; dead code de refactor. **Verificado.** |
| QW4 | Actualizar MCP.md "~27 tools" → "39" | docs/MCP.md:10, docs/MCP.md:119 | S | Número factual desfasado vs realidad. **Verificado (línea 10).** |
| QW5 | `count_documents({})` → `estimated_document_count()` | api/services/operaciones_informes.py:390 | S | O(1) vs scan de ~487k docs. |

Bonus de bajo costo (también S/bajo riesgo): proyección `{_id:1}` en
`mejoras_dispo.py:79` y `camara_cereales.py:46`; `@limiter.limit` en CRUD de
`manager/users.py:62,78,101`; `TimeoutStopSec=30s` + `StartLimitIntervalSec`
en los 13 motor `.service`.

---

## 3. Backlog por prioridad

### P0 — (ninguno abierto)

No hay caída activa. **PERO** los dos fixes del incidente CPU 100% del M10
están en el repo sin aplicar (ver P1.1 y P1.2) — son P1 "alto riesgo" porque
tocan índices en prod y requieren medir/ventana, no porque haya downtime ahora.

---

### P1 — Crítico (riesgo de prod / corrupción / pérdida de visibilidad)

#### Infra / DB (cierre del incidente recurrente)

**P1.1 — Índice UNIQUE parcial `boleto` → COLLSCAN 488k en upsert.**
`api/services/operaciones_informes.py:154`, `scripts/fix_indice_boleto.py`.
`uq_boleto` es UNIQUE+PARTIAL; los upserts hacen `{boleto: X}` sin el
`partialFilterExpression` → Mongo no lo usa → COLLSCAN de 488k (incidente
2026-06-04, causa C1). El fix existe pero **solo se corrió `--check`**.
Acción: `fix_indice_boleto.py --fix` fuera de rueda. **Esfuerzo S · Riesgo alto
· MEDIR ANTES.**

**P1.2 — TTL heredado fuera-de-banda en `Operaciones.OrdenesAudit.ts_1`.**
`scripts/fix_ordenes_audit_ttl.py`, `engines/motor_ordenes.py:101`. TTL puesto
a mano en Atlas → `create_index([('ts',1)])` plano choca → `OperationFailure
85 IndexOptionsConflict` → el motor muere (incidente 2026-06-05). Fix existe;
confirmar que se aplicó (ts_1 plano sin TTL). Regla: TTL SIEMPRE en código,
nunca en Atlas UI. **Esfuerzo S · Riesgo alto · CONFIRMAR ESTADO.**

#### Jobs / observabilidad

**P1.3 — Jobs críticos sin `JobRunLogger`.** `jobs/aum.py`,
`jobs/pnl_totales_precompute.py`, `jobs/consolidado_cuentas.py`,
`jobs/cashflow.py`, `jobs/bcra.py`, `jobs/snapshot_cierre.py`,
`jobs/negocio_movimientos.py`. Sin logger → fallos no disparan Telegram ni se
persisten en `Manager.JobRuns`; el watchdog solo ve "completó". Patrón:
`jobs/operaciones_informes.py:97`. **Esfuerzo S · Riesgo alto** (= QW1, los 3
más críticos primero: aum, pnl_totales, consolidado).

**P1.4 — `delete_many({}) + insert_many` NO atómico.**
`jobs/pnl_totales_precompute.py:33`, `jobs/consolidado_cuentas.py:31`. Ventana
de colección VACÍA entre borrar e insertar → `/pnl-todas` devuelve `[]` en
intraday. Usar `reemplazar_coleccion_atomico` (ya existe, lo usa ops_rollup) o
upsert+borrar faltantes. **Esfuerzo M · Riesgo bajo.**

#### Performance

**P1.5 — `movimientos_mes` usa regex sobre `cuenta` en vez de `id_cuenta`
indexado.** `api/services/valuaciones.py:1415`. **Verificado**: filtra
`{"cuenta": {"$regex": "^\\[<id>\\]"}}` evaluado por-doc, cuando `id_cuenta`
está denormalizado e indexado en NegocioMovimientos. Antipatrón idéntico al C5
ya corregido en `_grupos_scope.py:112`. **Esfuerzo S · Riesgo bajo · verificar
id_cuenta poblado con diag antes de deployar.**

**P1.6 — `ComercialCache` precompute SIGUE sin implementar.**
`api/services/comercial.py:759` (`informe_comercial`), `:834` (`debug_comercial`).
Agregan sobre NegocioMovimientos (~337k) SIN fecha ni cuenta → COLLSCAN cada
10min (TTL=600) compitiendo con motores. El docstring (`comercial.py:14`)
promete el precompute pero nunca se hizo; `_por_cuenta_cache()` devuelve `{}`.
Implementar `jobs/comercial_rollup.py` → `Clientes.ComercialCache` (post-aum,
off-peak). **Esfuerzo L · Riesgo bajo.**

#### Arquitectura

**P1.7 — Violación de capas: `jobs/` y `engines/` importan de `api/services/`.**
`jobs/consolidado_cuentas.py:1`, `jobs/pnl_totales_precompute.py:1`,
`jobs/negocio_movimientos.py:1`, `jobs/comercial_rollup.py:1` (+13 total). La
regla es `core→{engines,jobs}`, `api/services→puro`. Mover helpers/constantes
compartidos a `core/` y re-exportar en `api/services`. **Esfuerzo M · Riesgo
medio.**

**P1.8 — Duplicación crítica de constante `_TIPOS_DIVISOR_100`.**
`api/services/pnl.py:55` y `jobs/partner_export.py`. Mismos 6 tipos que dividen
por 100; cambiar uno y olvidar el otro = bug silencioso PnL vs export. Mover a
`core/constants.py`, importar en ambos. **Esfuerzo S · Riesgo alto** (la
desincronización corrompe valuaciones).

#### Frontend

**P1.9 — Megafile `manager-view.tsx` (3422 líneas, 30+ componentes anidados).**
`acaquant-web/src/components/manager-view.tsx:1`. **Verificado existe.** God
component sin reutilización (4 `CheckPanel` casi idénticos, types inline). El
caso más grave es `TabClientes` (~1000 líneas, 4 sub-tabs con boilerplate
duplicado, `:1660-2713`). Extraer a `components/manager/<dominio>/` + hooks
compartidos. **Esfuerzo L · Riesgo medio.**

#### Scripts / proceso

**P1.10 — No existe política de archivado de scripts.** `scripts/` (raíz),
CLAUDE.md. **Verificado: `scripts/archive/` no existe.** Sin mecanismo para
retirar one-shots ejecutados → `backfill_2025`, `rename_*`,
`quitar_operar_sales` ensucian el catálogo y arriesgan re-ejecución. Crear
`scripts/archive/` + INDEX.md + regla en CLAUDE.md. **Esfuerzo M · Riesgo
medio** (mover un script que algo importa rompe). Relacionado: backfills
históricos sin archivar — `scripts/backfill_2025.py:1`,
`scripts/rename_limite_fondeo_a_cupo.py:1`, `scripts/rename_nivel_valor.py:1`,
`scripts/quitar_operar_sales.py:1`.

#### Seguridad

**P1.11 — MCP OAuth: `/oauth/authorize` sin parámetro `state` (CSRF).**
`api/mcp/oauth.py:200-250`. PKCE mitiga parcial, pero RFC 6749 recomienda
`state` como defense-in-depth. Generar `secrets.token_urlsafe(32)`, guardar en
OAuthCodes, validar en token endpoint. **Esfuerzo M · Riesgo medio.**

---

### P2 — Importante (deuda que degrada, no rompe)

#### DB / índices

- **Índices redundantes en `CashFlow.Operaciones`** — `concertacion` single
  cubierto por `concertacion_mercado`. `scripts/drop_indices_redundantes_operaciones.py:23`,
  `api/services/operaciones_informes.py:353`. **MEDIR con $indexStats antes de
  dropear.** Esfuerzo M · bajo.
- **`distinct()` sin índice prefijo** — `api/routers/operaciones.py:883`
  (`mercado`), `:1354` (`segmento`). Subir TTL a 3600 (casi no cambian intra-día)
  o crear índices. Esfuerzo S · bajo.
- **Denormalización incompleta: `Operaciones` sin `id_cuenta`** —
  `jobs/negocio_movimientos.py:59`, `jobs/comercial_rollup.py:71`,
  `api/services/comercial.py:6`. Backfill `id_cuenta` (REGLA #4) o documentar que
  el volumen vive en NegocioMovimientos. Esfuerzo M · medio.
- **Índice parcial `commodity` puede no cubrir docs viejos** —
  `api/services/operaciones_informes.py:169`, `jobs/ops_rollup.py:50`. Confirmar
  materializado en todos los docs; `backfill_commodity_operaciones.py` existe.
  Esfuerzo M · bajo.
- **`boleto` UNIQUE partial difiere entre Flujo (int) y Operaciones (str/int)** —
  `scripts/crear_indices.py:99`, `api/services/operaciones_informes.py:155`.
  Medir docs con boleto string. Esfuerzo S · bajo.
- **Inconsistencia `timestamp` vs `fecha` vs `fecha_snapshot`** —
  `scripts/crear_indices.py:52`, `jobs/aum.py:239`. Documentar convención en
  CLAUDE.md/RUNBOOK. Esfuerzo M · medio.

#### Performance

- **`find({})` sin proyección** — `api/services/mejoras_dispo.py:79`,
  `api/services/camara_cereales.py:46`. Esfuerzo S · bajo.
- **`_arancel_match` con `$or` complejo (documentar, no cambiar)** —
  `api/routers/operaciones.py:872`. Agregar comentario del porqué (arancel
  caución vive solo en cierre). Esfuerzo S · bajo.

#### Arquitectura

- **Módulos gigantes** — `api/routers/operaciones.py:1` (1502 líneas, lógica
  Mongo mezclada con HTTP → extraer a `api/services/operaciones.py`),
  `api/services/comercial.py:1` (1053), `api/services/valuaciones.py:1` (1579,
  partir AuM-snapshot vs cost-basis). Esfuerzo M · bajo c/u.
- **Cache manual con `threading.Lock` re-inventado** —
  `api/routers/operaciones.py:37`, `api/routers/manager/jobs.py:26`. Usar
  `@cached` de `api/cache.py`. Esfuerzo S · bajo.
- **`except (TypeError, ValueError): pass` sin log (50+ matches)** —
  `api/services/*.py`, `engines/*.py`. Mínimo WARNING. Esfuerzo S · medio.
- **Duplicación renta_fija.py ↔ engines/curvas.py** — `api/services/renta_fija.py:207,571`.
  Mover funciones puras a `quant/`. Esfuerzo M · bajo.
- **Sin separación visual helpers privados/API pública en archivos grandes** —
  comercial.py, valuaciones.py, operaciones.py. Esfuerzo S · bajo.

#### Seguridad / ops

- **13 motor `.service` sin `TimeoutStopSec`** — `deploy/systemd/motor_*.service`.
  WebSocket colgado espera 90s default antes de SIGKILL. Agregar
  `TimeoutStopSec=30s`. Esfuerzo S · medio.
- **Motores sin circuit breaker** — `deploy/systemd/motor_rofex.service:10` (+todos).
  Crash-loop infinito cada 10s. Agregar `StartLimitIntervalSec=300`
  `StartLimitBurst=5`. Esfuerzo S · bajo.
- **CRUD de Users/RBAC sin rate-limit** — `api/routers/manager/users.py:62,78,101`.
  Esfuerzo S · bajo.
- **`CF_ACCESS_TEAM/AUD` no validados como bloqueantes en prod** —
  `api/main.py:63`. Esfuerzo S · medio.
- **Partner API sin revocación inmediata de tokens** — `partner_api/auth.py:64`.
  Esfuerzo M · bajo.
- **`PARTNER_EXPORT_CUENTAS` hardcodeado** — `config.py:128`. Mover a Mongo.
  Esfuerzo M · bajo.
- **`DOLAR_INGEST_TOKEN` y `CF_TRUSTED_SERVICE_TOKENS` sin versionado/audit** —
  `config.py:27,156`. Esfuerzo M · bajo.

#### Frontend

- **Error-handling inconsistente: `console.error` sin estado UI** —
  `manager-view.tsx:129,514`. Spinner infinito al fallar. Esfuerzo S · bajo.
- **`aum-view.tsx` (1835) sin `useMemo` en filtros/sorts de 1000+ filas** —
  `acaquant-web/src/components/aum-view.tsx:1`. **MEDIR con Profiler primero.**
  Esfuerzo M · bajo.
- **keep-alive `visited` Set sin cleanup garantizado** —
  `operaciones-view.tsx:20`. Esfuerzo M · bajo.
- **`OperacionesBackfillPanel`: `Record<string,unknown>` sin validación de
  schema en upload CSV** — `manager-view.tsx:3073,3118`. Validar con Zod
  (REGLA #2). Esfuerzo M · bajo.
- **`TabClientes` 4 sub-tabs con boilerplate duplicado** —
  `manager-view.tsx:1660-2713`. Esfuerzo L · medio.
- **`curvas-chart.tsx` ResponsiveContainer re-mide cada render** —
  `acaquant-web/src/components/curvas-chart.tsx:1`. **MEDIR primero.** Esfuerzo M · bajo.

#### Docs

- **`API.md` menciona asistente `/api/chat` como activo** —
  `docs/API.md:15,119,129,139,175,395,673`. El asistente fue ELIMINADO. Marcar
  Legacy/remover. Esfuerzo M · medio.
- **`acaquant-web/CLAUDE.md` es stub vacío (`@AGENTS.md`)** —
  `acaquant-web/CLAUDE.md`. Esfuerzo M · medio.
- **Vault README "199 módulos" posiblemente stale post-eliminación agente** —
  `docs/vault/README.md:9`. Correr `gen_obsidian --check`. Esfuerzo M · medio.
- **PDFs/PNGs/CSV orfanos sin referencia** — `docs/*.pdf` (3, ~2.3MB),
  `docs/img*.png` (3), `docs/seg_comitentes_template.csv`. Archivar/referenciar.
  Esfuerzo S · bajo.
- **`SECURITY_AUDIT_2026-05.md` C1 sin estado de resolución** — `:23-29`.
  Esfuerzo S · bajo.
- **`API.md` cambio motor_curvas 2026-05-04 disperso** — `:283,315,639`.
  Esfuerzo S · bajo.

#### Código muerto

- **Clase `AunesaApiManager` nunca instanciada** — `jobs/aunesa_client.py:12`.
  Verificar consumidor; si none, archivar/remover. Esfuerzo M · bajo.
- **`api/agent/` sin fuentes `.py` (solo `.pyc`)** — `api/agent/`.
  **Verificado: solo `__pycache__`.** Tag git + remover de main. Esfuerzo S · bajo.

#### Scripts

- **Cluster `diag_aranceles_*` (5) y `diag_fci_*` (7) sin cross-reference** —
  `scripts/diag_aranceles_*.py`, `scripts/diag_fci_*.py`. Esfuerzo M · bajo.
- **`diag_*_operadores_migracion` obsoletos post-migración** —
  `scripts/diag_volumen_operadores_migracion.py:1`,
  `scripts/diag_aranceles_operadores_migracion.py:1`. Archivar con fecha.
  Esfuerzo S · bajo.
- **Falta convención de metadatos (Tipo/Categoría/Estado) en docstrings** —
  `scripts/CLAUDE.md:6`. Esfuerzo M · bajo.
- **`HERRAMIENTAS.md` auto-generado incompleto (no cataloga diags)** —
  `docs/HERRAMIENTAS.md`, `scripts/gen_herramientas.py:1`. Esfuerzo M · bajo.
- **Scripts sin `--dry-run`/`--apply`** — `scripts/enrich_doc_comitentes.py`,
  `scripts/limpiar_cauciones_adhoc.py`. Esfuerzo M · medio.

---

### P3 — Menor (pulido, nomenclatura, conveniencia)

- Type hints faltantes en params Mongo — `api/services/pnl.py:79`, `valuaciones.py:51`. S·bajo.
- `_idempotencia.py` review de ramas muertas — `api/services/_idempotencia.py`. M·bajo.
- Validación de rango `desde<=hasta` en query params — `api/routers/operaciones.py`, `analitica.py`. S·bajo.
- Falta `docs/INDEX.md` de navegación — `docs/`. S·bajo.
- `API.md` versión 0.3.0 sin changelog — `docs/API.md:1`. S·bajo.
- `HERRAMIENTAS.md` no dice cuándo regenerar — `docs/HERRAMIENTAS.md:7`. S·bajo.
- `TABLERO_COMERCIAL.md` sin "campos finales UI" + link a submódulo — `docs/SEGMENTACION_PATRIMONIAL.md:5`. S·bajo.
- `api_migrate.py` docstring no aclara que es import-only — `scripts/api_migrate.py:1`. S·bajo.
- `diag_aum_backfill_log` específico de un job — `scripts/diag_aum_backfill_log.py:1`. S·bajo.
- Scripts infra sin marcador `Herramienta:` — `scripts/partner_user.py:1`, `lock_deps.py:1`, `watch_db.py:1`. S·bajo.
- `@cached` aplicado inconsistente (ej. `ops_meta` sin cache) — `api/routers/operaciones.py:900`. S·bajo.
- Constantes/proyecciones dispersas fuera de config.py — `api/services/comercial.py:39`, `api/routers/operaciones.py:43`. S·bajo.
- Rollup+live-fallback no aplicado fuera de operaciones — `api/routers/derivados_agro.py`. M·bajo.
- Sync handlers con queries Mongo sin cache intermedio — `api/routers/operaciones.py:55`. S·bajo.
- Cache-Control 'no-store' no en todas las routes Next — `acaquant-web/src/app/api/**/route.ts`. S·bajo.
- `LogsPanel` SERVICES_FALLBACK puede quedar stale — `logs-panel.tsx:8`. S·bajo.
- Imports estáticos de panels en bundle manager — `manager-view.tsx:16`. S·bajo.
- Partner API routes sin doc de segregación de datos — `partner_api/routes.py`. S·bajo.
- Logs de cron sin cabecera job_name+timestamp — `deploy/run_job.sh`. S·bajo.

---

## 4. Roadmap por fases

**Fase 0 — Estabilizar prod (esta semana, fuera de rueda).**
Aplicar los fixes ya escritos del incidente CPU 100% (`fix_indice_boleto.py
--fix`, confirmar `fix_ordenes_audit_ttl.py`) midiendo antes; envolver los 3
jobs críticos en JobRunLogger (QW1) y arreglar los swaps no-atómicos
(P1.3+P1.4). Cierra la causa raíz #1 de caídas.

**Fase 1 — Quick wins + observabilidad (semana 1-2).**
QW2-QW5 + rate-limits Manager + `TimeoutStopSec`/circuit-breaker en motores +
fix regex `cuenta` (P1.5). Bajo riesgo, alto retorno operativo.

**Fase 2 — Performance estructural (semana 2-4).**
Implementar `Clientes.ComercialCache` (P1.6, el COLLSCAN recurrente que compite
con motores) + medir/dropear índices redundantes con $indexStats + denormalizar
`id_cuenta` en Operaciones. Soporta los 7-8 usuarios de la mesa.

**Fase 3 — Aislamiento de capas y fuente única (semana 4-6).**
Mover constantes/helpers compartidos a `core/` (P1.7), eliminar duplicación
`_TIPOS_DIVISOR_100` (P1.8), partir los 3 megafiles backend en services finos.

**Fase 4 — Mantenibilidad frontend (semana 6-8).**
Descomponer manager-view.tsx + TabClientes en `components/manager/<dominio>/`
con hooks compartidos, estandarizar error-handling y validación de uploads.

**Fase 5 — Higiene de scripts y docs (continuo, baja prioridad).**
Crear `scripts/archive/` + convención de metadatos + regenerar HERRAMIENTAS/
vault; actualizar API.md (quitar asistente), crear docs/INDEX.md, archivar
artefactos orfanos; endurecimiento OAuth state + versionado de tokens.

---

## 5. MEDIR ANTES DE TOCAR (REGLA #2 — bloqueante)

Ninguno de estos se ejecuta sin el dato de prod primero. Todos vía script
read-only que corre el usuario en el Droplet/Atlas, devuelve el número, y
recién ahí se decide.

| Acción | Qué medir | Cómo | Riesgo si se asume |
|---|---|---|---|
| **Aplicar `fix_indice_boleto.py --fix`** | Costo del create sobre 488k docs + que `explain()` use IXSCAN post-fix | `--check` (ya corrido) + explain; correr **fuera de rueda** (no 13-20 UTC L-V) | Tiró CPU 100% 2x (incidente 2026-06-03/04). Crear índice en rueda starva motores. |
| **Dropear índices redundantes** (`concertacion` y prefijos) | `$indexStats` → ops reales por índice en 48h de carga normal | Crear `scripts/diag_index_stats.py` (NO existe — **verificado**), correr en prod, pegar output en `docs/INDEX_STATS_LATEST.md` | Dropear un índice "redundante en teoría" que en realidad sirve un plan caliente = regresión de perf. |
| **Backfill `id_cuenta` en Operaciones** | Count de docs sin `id_cuenta` + que el regex de extracción matchee el formato `[805] NOMBRE` | `scripts/diag_*` read-only; scopeado + batcheado + throttle (REGLA #4), fuera de rueda | Scan 488k a ciegas en rueda = el anti-patrón del incidente. |
| **Fix regex `cuenta`→`id_cuenta`** (P1.5) | Que `NegocioMovimientos.id_cuenta` esté 100% poblado | `scripts/diag_scope_cuenta` antes de deployar | Si hay docs sin `id_cuenta`, la nueva query devuelve menos filas (pérdida silenciosa de movimientos). |
| **Backfill `commodity`** | Count docs Operaciones con `commodity` missing/null | diag read-only; `backfill_commodity_operaciones.py` ya existe | Queries agro a COLLSCAN si el parcial no cubre. |
| **Inconsistencia `boleto` Flujo(int) vs Operaciones(str)** | Count docs Operaciones con `{boleto:{$type:'string'}}` | diag read-only | Cambiar el partial sin saber si hay strings puede romper upserts. |
| **Confirmar TTL OrdenesAudit aplicado** (P1.2) | Que `ts_1` sea plano (sin `expireAfterSeconds`) | diag de índices read-only sobre OrdenesAudit | Si sigue con TTL, el motor vuelve a morir al reiniciar. |
| **Memoización frontend** (aum-view, curvas-chart) | Re-renders reales con React DevTools Profiler (>16ms) | Profiler en staging | Optimizar sin medir = trabajo sin retorno (premature optimization). |
| **Lazy-load de panels manager** | Tamaño de bundle por panel (DevTools Coverage) | Coverage en build | Romper navegación "snappy" deliberada (imports estáticos son intencionales). |
| **Archivar/remover scripts y código** (AunesaApiManager, api/agent, backfills, migracion diags) | Grep de imports activos + git log de último uso antes de mover/borrar | `grep` de referencias + revisar quién importa | Mover un script que un job importa rompe el cron (relacionado con REGLA #1). |

> **Cierre de incidente conocido:** P1.1 y P1.2 NO son hallazgos nuevos — son
> los fixes del incidente CPU 100% que ya están escritos en el repo y solo
> falta **aplicarlos con medición y ventana**. Son la máxima prioridad operativa.
