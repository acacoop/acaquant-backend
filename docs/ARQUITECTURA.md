# ARQUITECTURA — documento madre de TradingAV

> **Este es el único documento de arquitectura, datos y estrategia.** Reemplaza a
> `ESTRATEGIA_TECNICA.md`, `ESTADO_DATOS_2026-06.md` y las auditorías sueltas
> (todas borradas y consolidadas acá). Escrito para producto + técnico: explica el
> *qué*, el *porqué* y la *secuencia*. Se actualiza a medida que avanzamos.
>
> **Docs de referencia operativa (separados, vivos):** `API.md` (endpoints),
> `RUNBOOK.md` (operación/incidentes), `MCP.md`/`MCP_TOOLS.md` (MCP server),
> `MOTOR_VALUACIONES.md` (PnL), `INGEST_DOLAR.md` (feed dólar), `SECRETS.md` +
> `SECURITY.md` (seguridad), `PARTNER_API*.md`, `TABLERO_COMERCIAL.md`,
> `GRUPOS.md`, `SEGMENTACION_PATRIMONIAL.md`, `HERRAMIENTAS.md` (auto-gen).
> El plano vivo de servicios/crons: `deploy/SISTEMA.md`. El grafo navegable:
> `docs/vault/`.

Última actualización: 2026-06-06.

---

## 1. El sistema en una página

pyRofex WS → MongoDB Atlas M10 → FastAPI (`api.acaquant.com`) → Next.js en Vercel
(`trading.acaquant.com`). Server en un Droplet DO. Motores WS always-on L-V,
jobs/crons batch, rollups precalculados. ~30 usuarios hoy, objetivo 200+.

- **Operativo (la mesa):** curvas, forwards, breakevens, opciones, órdenes (OPERAR),
  operaciones/negocio, portfolios/AuM, scanner.
- **Comercial/back-office:** clientes, segmentación, operadores, aranceles, FCI.
- **Plataforma:** Manager (RBAC, diagnóstico, ingesta), MCP server, Partner API.

---

## 2. El modelo mental (leer primero)

Cada decisión de arquitectura es una balanza entre **capacidad/escala** y
**complejidad/costo/fragilidad**. La pregunta NO es *"¿es moderno?"* sino
**"¿la capacidad que da justifica la complejidad que agrega EN MI ETAPA?"**

Tres principios:
1. **Nada se descarta — se secuencia.** Lo "avanzado" se agrega cuando dispara un
   **gatillo medible**, no por moda. Antes del gatillo es complejidad prematura.
2. **La complejidad la absorbe la ingeniería + el sistema se autodefiende.** Que el
   user sea uno solo y no-dev no veta tecnología potente: obliga a construirla bien,
   documentada y con guardrails.
3. **Robusto-hoy y potente-mañana no son enemigos: son una línea de tiempo.**

---

## 3. Estado de los datos (verificado contra el cluster, 2026-06)

**Cluster:** ~775 MB datos · ~325 MB índices · 16 → **11 DBs** (se eliminaron 5
espejos, ver §4). El 82% del peso de índices vive en 3 colecciones:
`CashFlow.Operaciones` (157 MB idx), `CashFlow.NegocioMovimientos` (64 MB),
`Valuaciones.AuM` (44 MB).

**Lo resuelto:**
- ✅ **5 espejos `*API` eliminados** (CuentasAPI, OperacionesAPI, PortfolioAPI,
  TitulosAPI×2). La API lee las fuentes directo; el join Curvas+Bonds y la
  normalización de Assets viven en `api/services/titulos_flujos.py`. Se borraron
  `sync_api_copies` y `api_migrate`. −5 DBs, ~360 líneas menos.
- ✅ **`scripts/` 120 → 44** (one-shots ya cumplidos borrados).
- ✅ **`perf_scan` bloqueante en CI** (anti-patrones Mongo: N+1, queries sin
  proyección, etc.). En cero hoy.
- ✅ **Índice boleto** (causa de los CPU 100%) resuelto.

**Lo pendiente (medido / por medir):**
- ⚠️ **Dólar fragmentado en 4 colecciones / 2 DBs** (`Trading.DOLAR`,
  `Valuaciones.{Dolar, DolarOficialLive, DolarSnapshot}`) → fuente única (§7, Fase 2).
- ⚠️ **Índices muertos sin medir:** ni el user RO ni el RW tienen `$indexStats`;
  el Performance Advisor de Atlas no marca drops. **PENDIENTE:** crear user Atlas
  con rol `clusterMonitor` para medir uso real (REGLA #6 — pedido una vez).
- ⚠️ **`Trading` = cajón de 39 colecciones** (market data + macro + dólar + cedears
  + agro + snapshots juntos) → reordenar por dominio (Fase 3, alto riesgo).
- ⚠️ Colecciones vacías con índices: `Manager.Grupos`, `Operaciones.BracketsLive`,
  `Valuaciones.{AuMResumen, AumBackfillRuns}` → dropear.

---

## 4. Decisiones de arquitectura (las dos miradas, condensado)

| Tema | Veredicto para TradingAV hoy | Gatillo para escalar |
|---|---|---|
| **Sync vs Async** | Sync alcanza (1 proceso, 30 users). Thread-pool donde duela. | Requests encolando bajo carga real (CPU baja, latencia alta) |
| **Cache in-proc vs Redis** | In-process es correcto con 1 worker. | >1 worker/servidor + inconsistencia notada, o rate-limit serio |
| **Rollups a mano vs primitivo** | Ya hay 5 copias del patrón → **unificar** en `core/materialized` (refactor que RESTA complejidad). | Ya disparó |
| **Monolito vs microservicios** | **Monolito sí o sí.** Modularizar por dentro (partir megafiles). | Equipo de varias personas |
| **Capa de confianza de datos** | **Donde más rinde invertir** (dolor #1). Contratos de ingesta + reconciliación + SLAs de completitud. | Ya disparó |
| **SQL para lo relacional** | **Empezar ahora, como capa analítica/reporting (riesgo cero), NO rip-and-replace.** Ver §5. | Reportería cruzada / BI / ML |

---

## 5. SQL (Postgres) para el núcleo relacional — el plan

### Por qué SQL acá (el concepto)
Tu sistema tiene **dos naturalezas**: datos **relacionales** (clientes, cuentas,
operaciones, AuM, contrapartes, comercial — todo cruzado por `id_cuenta`) y datos
**documentales/time-series** (mercado: TimeSales, snapshots, curvas, opciones). SQL
le va **mejor a lo relacional** (joins, agregaciones, reportería, constraints son su
idioma); Mongo le va mejor al mercado. Usar cada uno para lo suyo = **persistencia
poliglota**. Los "app-joins" que hoy sufrís en Python son exactamente lo que SQL
hace nativo.

### La trampa a evitar
Migrar TODO a SQL de golpe = meses, alto riesgo, y NO arregla el cuello real (que es
modelado/índices, no el motor). **No se hace big-bang.**

### El camino seguro (empezar ya, sin tocar la operación)
Postgres entra primero como **capa analítica/reporting de SOLO LECTURA**, alimentada
desde Mongo. Cero riesgo para la mesa (si se cae, la operación sigue intacta), y te
da YA el modelo relacional limpio + la base para reportería y ML.

| Fase | Qué | Riesgo | Entregable |
|---|---|---|---|
| **A. Setup + esquema** | Levantar Postgres (managed: Neon / Supabase / DO Managed PG). Diseñar el esquema relacional del núcleo: `cuentas`, `comitentes`, `operaciones`, `aum`, `contrapartes`, `operadores` con sus FKs e índices. | Nulo (infra nueva, aislada) | DB + esquema versionado (`sql/schema.sql`) |
| **B. Sync Mongo→PG** | Job que replica esas colecciones a las tablas PG (incremental, idempotente). Validar que los números cuadran contra Mongo. | Bajo (read-only de Mongo, write a PG) | `jobs/sync_postgres.py` + reconciliación |
| **C. Reportería sobre PG** | Las vistas de **reporting/comercial/cruces** (no la operación en vivo) leen de PG con SQL real. Lo operativo sigue en Mongo. | Bajo (nueva ruta de lectura) | endpoints de reporting sobre PG |
| **D. (futuro) PG fuente de verdad de lo relacional** | Solo si C demuestra valor: invertir el flujo para el núcleo relacional, dejando mercado en Mongo. | Alto (RED, dual-write, medido) | decisión con datos, no antes |

**Primer paso concreto (lo que arrancamos):** Fase A — elegir el Postgres managed,
escribir `sql/schema.sql` del núcleo relacional, y un `docs/SQL.md` con el modelo.
Sin tocar nada de prod. Decisión tuya: ¿qué proveedor de Postgres? (recomiendo
**Neon** o **DO Managed Postgres** por simplicidad de operar solo).

---

## 6. Roadmap integrado

| Fase | Qué | Estado |
|---|---|---|
| **0. Estabilizar** | CPU/índice/TTL, JobRunLogger en jobs, watchdog | ✅ hecho |
| **1. Confianza de datos** | Contratos de ingesta + reconciliación + SLAs de completitud (extender Diagnóstico de "¿vivo?" a "¿completo/correcto?") | 🔜 próximo foco |
| **2. Fuente única** | Dólar (4→1), unificar primitivo de rollups (`core/materialized`) | pendiente |
| **3. Modularizar + reordenar DBs** | Partir megafiles; reordenar `Trading` por dominio (migración segura, dual-write) | pendiente |
| **4. SQL relacional** | Postgres como capa analítica/reporting (§5) | 🆕 arrancando (Fase A) |
| **5. Escalar** | Workers + Redis + async donde duela, medido | cuando dispare |
| **6. Cerebro (ML/BI)** | Analítica/modelos sobre datos limpios (PG es el habilitador natural) | sobre 1-4 |

**Higiene permanente (reglas):** `scripts/` y `docs/` minimalistas (REGLA #5);
ir más allá / enseñar (REGLA #7); medir antes de tocar prod (REGLA #2).

---

## 7. Fuente única del dólar (Fase 2, detalle)
Hoy "el dólar" vive en `Trading.DOLAR` (BCRA A3500), `Valuaciones.Dolar` (histórico),
`Valuaciones.DolarOficialLive` (feed MAE), `Valuaciones.DolarSnapshot`. **Objetivo:**
una colección fuente con campo `tipo` (oficial/mayorista/mep/a3500) + `origen`, con
adaptadores en los services para no romper consumidores. Migración con dual-write +
backfill idempotente. Bajo riesgo, alto valor didáctico — buen primer caso de
consolidación.
