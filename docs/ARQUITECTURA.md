# ARQUITECTURA — documento madre de TradingAV

> **Este es el único documento de arquitectura, datos y estrategia.** Reemplaza a
> `ESTRATEGIA_TECNICA.md`, `ESTADO_DATOS_2026-06.md` y las auditorías sueltas
> (todas borradas y consolidadas acá). Escrito para producto + técnico: explica el
> *qué*, el *porqué* y la *secuencia*. Se actualiza a medida que avanzamos.
>
> **Docs de referencia operativa (separados, vivos):** `API.md` (endpoints),
> `RUNBOOK.md` (operación/incidentes),
> `MOTOR_VALUACIONES.md` (PnL), `SECURITY.md` +
> `SECURITY.md` (seguridad),
> `CLIENTES.md`, `CLIENTES.md`, `HERRAMIENTAS.md` (auto-gen).
> El plano vivo de servicios/crons: `deploy/SISTEMA.md`.

Última actualización: 2026-08-30.

> ⚠️ Este doc se quedó en el decomiso de Mongo (2026-06-29) y no registraba nada
> de lo que pasó después: el **AV AGENT** (`docs/AGENT.md`), **research** (1816,
> BCRA, FRED), **interbanking**, **postrade** ni la **API externa** para accionistas.
> Están todos en el «Mapa de docs» del `CLAUDE.md` raíz — que es hoy el índice real.

---

## 1. El sistema en una página

pyRofex WS → **Postgres/Supabase** → FastAPI (`api.acaquant.com`) → Next.js en Vercel
(`trading.acaquant.com`). Server en un Droplet DO. Motores WS always-on L-V,
jobs/crons batch, caches precalculados donde pesa. ~30 usuarios hoy, objetivo 200+.

> **Mongo decomisado (2026-06-29).** Postgres/Supabase es la **única** base de
> datos: todos los motores, jobs y services leen/escriben SQL nativo. El cluster
> Atlas M10 y el cliente Mongo del repo fueron eliminados. Modelo y schemas:
> `docs/SQL.md` + `sql/schema.sql`.

- **Operativo (la mesa):** curvas, forwards, breakevens, opciones, órdenes (OPERAR),
  operaciones/negocio, portfolios/AuM, scanner.
- **Comercial/back-office:** clientes, segmentación, operadores, aranceles, FCI.
- **Plataforma:** Manager (RBAC, diagnóstico, ingesta). El **MCP server** se apagó y se borró el 2026-08-28 — el producto no tiene asistente conversacional.

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

## 3. Estado de los datos (post-decomiso Mongo, 2026-06-29)

**Base única: Postgres/Supabase**, **17** schemas de dominio: `aca`, `agente`,
`ap5`, `bancos`, `clientes`, `estrategia`, `ext`, `home`, `ia`, `macro`,
`manager`, `mercado`, `operaciones`, `partner`, `portafolio`, `research`,
`valuaciones`. (Decía «10» y listaba 8.)
Modelo completo + inventario de tablas: `docs/SQL.md`. Schema fuente:
`sql/schema.sql`.

**Lo resuelto (recorrido completo):**
- ✅ **Decomiso de Mongo COMPLETO** (2026-06-29): toda la lectura y escritura es
  SQL-native; el cliente Mongo, `api/db.py`, `core/mongo*.py` y el tooling Mongo
  fueron borrados del repo, y el cluster Atlas M10 se terminó. No hay dual-run,
  flags de engine ni "espejo read-only": cada dominio lee/escribe su schema SQL
  (`api/services/<x>_sql.py`, writers vía `core/pg_mirror` native).
- ✅ **Renta variable, operaciones, negocio, tenencias/AuM, clientes, valuaciones,
  órdenes, mercado, opciones, agro, macro, manager, home** — todos SQL-native.
- ✅ **`perf_scan` en CI** (anti-patrones de queries). Suite unit verde.

**Lo pendiente (operación, no código):**
- ⚠️ Sacar del `.env`/systemd del Droplet las env vars Mongo colgadas (`MONGO_URI`,
  `ATLAS_*`, `PARTNER_MONGO_URI`) — el código ya no las lee.
- ⚠️ Higiene `scripts/`: borrar comparadores `compare_*_sql_vs_mongo.py` y restos de
  tooling Atlas que ya no aplican (REGLA #5).

---

## 4. Decisiones de arquitectura (las dos miradas, condensado)

| Tema | Veredicto para TradingAV hoy | Gatillo para escalar |
|---|---|---|
| **Sync vs Async** | Sync alcanza (1 proceso, 30 users). Thread-pool donde duela. | Requests encolando bajo carga real (CPU baja, latencia alta) |
| **Cache in-proc vs Redis** | In-process es correcto con 1 worker. | >1 worker/servidor + inconsistencia notada, o rate-limit serio |
| **Monolito vs microservicios** | **Monolito sí o sí.** Modularizar por dentro (partir megafiles). | Equipo de varias personas |
| **Capa de confianza de datos** | **Donde más rinde invertir** (dolor #1). Contratos de ingesta + reconciliación + SLAs de completitud. | Ya disparó |
| **SQL para lo relacional** | ✅ **Hecho.** Postgres/Supabase es la base única (decomiso Mongo 2026-06-29). Ver §5. | — (completado) |

---

## 5. Postgres/Supabase — la base única (migración COMPLETA)

### Por qué SQL (el concepto)
El núcleo del sistema es **relacional** (clientes, cuentas, operaciones, AuM,
contrapartes, comercial — todo cruzado por `id_cuenta`) y SQL le va nativo: joins,
agregaciones, reportería y constraints son su idioma. Los "app-joins" que antes se
hacían a mano en Python ahora son `GROUP BY` + índices. El mercado (snapshots,
curvas, time-series) también vive en SQL: columnas tipadas + `jsonb` para lo anidado.

### Cómo se hizo (camino seguro, sin big-bang)
La migración NO fue rip-and-replace. Postgres entró primero como capa de lectura
dual-run (SQL **o** Mongo según flag, path Mongo intacto → rollback = sacar el flag),
dominio por dominio, con un comparador SQL↔Mongo como gate antes de cada cutover.
A medida que cada dominio pasó a SQL source-of-truth, se cortó su lado Mongo.
**El proceso terminó el 2026-06-29** con el decomiso completo: ya no quedan flags,
dual-run ni cliente Mongo en el repo.

| Fase | Qué | Estado |
|---|---|---|
| **A. Setup + esquema** | Supabase Postgres + `sql/schema.sql` (hoy 17 schemas de dominio). | ✅ |
| **B. Lectura SQL por dominio** | `api/services/<x>_sql.py` + comparador SQL↔Mongo como gate. | ✅ |
| **C. Escritura SQL-native** | Motores/jobs escriben SQL (`core/pg_mirror`); se cortó Mongo. | ✅ |
| **D. Decomiso Mongo** | Cliente Mongo y tooling borrados; cluster Atlas terminado. | ✅ (2026-06-29) |

Detalle del modelo, schemas, tablas y convenciones: **`docs/SQL.md`**.

---

## 6. Roadmap integrado

| Fase | Qué | Estado |
|---|---|---|
| **0. Estabilizar** | CPU/índice/TTL, JobRunLogger en jobs | ✅ hecho |
| **1. Confianza de datos** | Contratos de ingesta + reconciliación + SLAs de completitud (extender Diagnóstico de "¿vivo?" a "¿completo/correcto?") | 🔜 próximo foco |
| **2. SQL como base única** | Migrar Mongo→Postgres dominio por dominio + decomiso de Mongo (§5) | ✅ hecho (2026-06-29) |
| **3. Modularizar** | Partir megafiles; ordenar el código por dominio | parcial / continuo |
| **4. Escalar** | Workers + Redis + async donde duela, medido | cuando dispare |
| **5. Cerebro (ML/BI)** | Analítica/modelos sobre datos limpios (Postgres es el habilitador natural) | sobre 1-3 |

**Higiene permanente (reglas):** `scripts/` y `docs/` minimalistas (REGLA #5);
ir más allá / enseñar (REGLA #7); medir antes de tocar prod (REGLA #2).

---

## 7. El dólar en SQL (detalle)
El dólar vive en `valuaciones.{dolar, dolar_snapshot, dolar_oficial_live}` +
la serie A3500 en `macro.series_macro` (`serie='DOLAR'`, fixing BCRA diario).
`valuaciones.dolar_oficial_live` es la única fuente live (feed MAE mayorista UST$T
plazo 000). Los services leen el tipo que necesitan; para series macro
(`dolar_oficial`/`dolar_mayorista`) se usa `macro.series_macro`. (Consolidar las
tres tablas de `valuaciones` en una sola con campo `tipo` sigue siendo una mejora
posible de modelo, ya de bajo riesgo al estar todo en SQL.)
