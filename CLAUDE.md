# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

TradingAV — plataforma quant MERVAL/ROFEX. pyRofex WS → MongoDB Atlas M10 → FastAPI (`api.acaquant.com`) → **acaquant-web** Next.js en Vercel (`trading.acaquant.com`). Server en `/root/TradingAV` (Droplet DO), venv en `/root/TradingAV/venv`.

**DBs Mongo** (12 bases, inventario medido 2026-06-12 con `scripts/diag_inventario_mongo_sql`):
- `Trading` — núcleo de mercado: Curvas, BondsMaster, MarketSnapshot, SnapshotsCierre, CanjeCierre, TimeSales; series macro DOLAR/CER/BADLAR/TAMAR/RiesgoPais/InflacionMensual/InflacionInteranual/UVA; renta fija derivada BreakevensLive/Historico, ForwardsLive/Historico/Zscore, FitParams, FairValueResiduos, DiasHabiles, REM; FuturosDLR(+Snapshot), Caucion(+Snapshot); renta variable Cedears (master), CedearsTimeSales (tape intradía, se vacía al cierre); AgroSnapshot, AgroOpcionesSnapshot; SnapshotsSinteticos; ONSnapshot. **Renta variable migrada a SQL (cutover 2026-06-24): `CedearsSnapshot`→`mercado.cedears_snapshot`, `AdrSnapshot`→`mercado.adr_snapshot`, `PreciosAcciones`→`mercado.precios_acciones` (Mongo `PreciosAcciones` DROPEADA). `motor_cedears`/`adr_live`/`precios_acciones_daily` escriben SQL-native; scanner/day_trading/pivot_points leen SQL — ver `docs/SQL.md`.**
- `Valuaciones` — ConsolidadoCuentas, PnLTotalesCache, TenenciaHD, Dolar/DolarSnapshot/DolarOficialLive (MEP/CCL). **`AuM` y `Assets` ELIMINADAS (2026-06-15)** → tenencias y catálogo de títulos viven en SQL `portafolio.tenencia` / `portafolio.assets` (ver `docs/SQL.md`).
- `CashFlow` — Productores, Accionistas, Acreencias, Movimientos, VolumenMercadoAgro, TiposOperacion (catálogo). **`Operaciones`, `NegocioMovimientos`, `Contrapartes` y el rollup `OpsSerieDiaria` → migrados a SQL (`operaciones.operaciones`, `operaciones.negocio_movimientos`, `clientes.contrapartes`) y ELIMINADOS de Mongo (2026-06-16). Ver `docs/SQL.md`.**
- `Clientes` — Comitentes, ComercialCache, ActividadMensual (segmentación/operador asignado).
- `Manager` — Users, RoleMatrix, RoleAudit, Grupos, JobRuns, HealthReports, WatchdogAlertas.
- `Opciones` — opciones financieras GGAL (motor `engines/options.py`): Data (tick TS), DataHistorica, OptionsSnapshot, Metadata, VR-GGal.
- `Derivados` — carga MANUAL de la mesa (vista Agro): AgroPizarra, CamaraCereales (+ `*Audit`).
- `Operaciones` — motor de órdenes: OrdenesLive, OrdenesAudit, TriggersMep, BracketsLive, OperativasMep.
- `Market` — Quotes (watchlist HOME), EconomicCalendar.
- `News` — Headlines (TTL 2 días).
- `MCP` — OAuth clients/codes/tokens (TTL automático).
- App separada `partner_api` usa la base `ACAPortfolio` (Cartera, ApiUsers).

> Las colecciones espejo `*API` (`CuentasAPI`) fueron **eliminadas** (2026-06-06) — ver más abajo en "Deploy".

## Contexto por subdirectorio

Cada carpeta grande tiene su propio `CLAUDE.md` con lo que aplica SOLO ahí —
se carga automáticamente al trabajar en esa carpeta. Este archivo (raíz)
tiene lo que aplica a todo el repo.

- **`api/CLAUDE.md`** — ⚠️ REGLA #1 (validar imports), RBAC, services `@cached`, filtros de cuenta, live fallback, motor de PnL.
- **`engines/CLAUDE.md`** — patrón de escritura a `MarketSnapshot`, Atlas / motores stale.
- **`jobs/CLAUDE.md`** — filtros de exclusión del AuM, patrón de jobs nuevos (`JobRunLogger`).
- **`scripts/CLAUDE.md`** — REGLA #0 aplicada, minimalismo (REGLA #5), backfills seguros (REGLA #4).

## ⚠️ REGLA #0 — Cómo entregar trabajo al usuario (LEER PRIMERO)

**Claude NO tiene ni va a tener acceso al Droplet.** Todo lo que tenga que correr en producción se entrega como código en el repo, no como comando para copiar.

- **Nada de bloques de comandos / queries / snippets para que el user copie y pegue.** Operar el Droplet desde la consola web de DigitalOcean hace que copiar y pegar sea doloroso (line wrapping, multilinea, caracteres especiales). Esta regla ya se pidió varias veces y se sigue violando.
- **Workflow correcto**: Claude escribe el código → archivo en el repo (`scripts/<x>.py`, `jobs/<x>.py`, endpoint en `api/`) → commit + push a `main` → el user hace `git pull` en el Droplet y lo ejecuta con `python -m scripts.<x>`.
- **Diagnóstico one-shot también va a `scripts/`** (ej. `scripts/diag_*.py`). Una query Mongo de 5 líneas igual va en archivo, no en chat.
- **Excepción mínima**: si es UNA sola línea trivial (`systemctl status x`, `tail logs`), se puede pasar inline — pero el default es siempre script.
- **Cero "probá esto, si no andá probá esto otro"**. Una solución por vez, comiteada al repo.

## ⚠️ REGLA #2 — NUNCA ASUMIR: verificar antes de afirmar o codear

**Bloqueante. Es la causa #1 de romper cosas.** Claude NO tiene acceso al
Droplet ni a Atlas → no puede inferir nada sobre los datos reales. Afirmar
hechos sobre prod sin medir (proporciones, volúmenes, esquema, qué campos
existen, qué valores tienen, cómo se comporta algo) y después escribir código
en función de eso es lo que rompe todo.

- **Prohibido afirmar hechos no verificados sobre los datos/prod.** Nada de
  "X es una minoría", "esto normalmente trae…", "probablemente el campo…",
  "la mayoría de los docs…". Si no lo mediste, no es un hecho.
- **Distinguir SIEMPRE hipótesis de hecho verificado**, explícito y en voz alta.
  "Hipótesis (sin medir): …" vs "Verificado (corriste el diag): …".
- **NUNCA escribir código cuya CORRECTITUD dependa de una suposición no
  verificada.** Si la decisión necesita un dato de prod, primero conseguirlo.
- **Para conseguir un dato de prod**: escribir un diag read-only
  (`scripts/diag_*.py`, REGLA #0), el user lo corre y devuelve el número. Recién
  ahí se decide/codea. Si no se puede medir, decir explícito "no puedo verificar
  esto" y esperar confirmación — no avanzar a ciegas.
- **Optimizar = medir primero** (`explain()` / timing), después tocar. Nada de
  optimizaciones justificadas por una corazonada sobre cómo lucen los datos.

## ⚠️ REGLA #3 — TODO cambio lleva EXPLICACIÓN EJECUTIVA (a raja tabla)

**Obligatorio, sin excepción.** Cada cambio que se entrega (commit, script,
endpoint, fix, refactor, config) va acompañado de una explicación ejecutiva
breve, en lenguaje claro (no técnico-críptico), con DOS partes:

- **Qué soluciona** — el problema concreto que resuelve / la pregunta que
  responde. Por qué se hizo.
- **Qué genera / qué impacto tiene** — qué cambia en el sistema a partir de
  ahora: comportamiento nuevo, efectos colaterales, qué hay que correr/deployar,
  qué se gana (perf, plata, visibilidad), qué riesgo introduce si lo hay.

Formato sugerido (corto, va en el mensaje al user, no necesariamente en el código):

```
📋 Qué soluciona: …
📋 Qué genera:    …
```

El user opera solo un proyecto enorme y necesita entender el "qué" y el "para
qué" de cada cambio sin leer el diff. Un cambio sin esta explicación está
INCOMPLETO. Aplica también a los diags y a los cambios de doc.

## ⚠️ REGLA #4 — Backfills/migraciones JAMÁS escanean prod a ciegas

**Causó dos veces el CPU 100% en el M10. Bloqueante.** Ningún backfill,
migración o `--full` se corre sin cumplir TODO esto:

- **Scopeado**: apuntar SOLO a los docs que realmente cambian (ej. `bruto=0`),
  nunca un scan de toda la colección si se puede filtrar por índice.
- **Batcheado + throttle**: procesar en lotes con `sleep` entre lotes para no
  starvar a los motores. Nada de un `bulk_write` gigante de una.
- **Medir el costo ANTES** (REGLA #2): `explain()` / contar docs afectados. Si
  toca un scan grande, decirlo explícito y decidir.
- **Vía `run_job.sh`** (lock + timeout) y, salvo que sea liviano y scopeado,
  **fuera de rueda** (no 13-20 UTC L-V, cuando corren los motores).
- **Idempotente**: cortarlo a la mitad y re-correrlo no rompe nada.

Un `--full` a ciegas en horario de mercado es exactamente el anti-patrón del
incidente 2026-06-03. Si dudás del volumen, NO lo corras: medí primero.

## ⚠️ REGLA #5 — Minimalismo en `scripts/` Y `docs/`: se BORRA lo cumplido

**Minimalismo, no cementerio.** Aplica a código Y documentación.

- **`scripts/`:** cada `diag_*`/`fix_*`/`backfill_*`/`seed_*` one-shot, una vez que
  el user confirma que el tema cerró, **se elimina**. Queda solo lo recurrente
  (generadores, monitoreo, perf, audit, feeds) + lo referenciado por skills/CI/cron.
- **`docs/`:** las auditorías/análisis point-in-time, los `wip_*`, las imágenes
  scratch y todo lo superseded **se borran o se consolidan**. La arquitectura/
  datos/estrategia/roadmap viven en **UN doc madre: `docs/ARQUITECTURA.md`** — no
  esparcidos en N archivos. El resto de `docs/` es referencia operativa viva (API,
  RUNBOOK, MCP, seguridad, etc.) + el `vault/` auto-generado.
- Ante la duda, preguntar "¿lo borro?" — no acumular por las dudas.

## ⚠️ REGLA #6 — Credencial/acceso faltante: se pide UNA vez, no se insiste

Si para avanzar hace falta una credencial/usuario/permiso que **solo el user
puede crear** (ej. usuario Atlas con `clusterMonitor` para `$indexStats`), se
dice **una vez**, claro, y se marca como PENDIENTE. **No repetir el pedido cada
turno ni bloquear todo en eso** — seguir con lo que sí se puede hacer. El user
lo provee cuando puede.

## ⚠️ REGLA #7 — IR MÁS ALLÁ: enseñar y proponer, no solo ejecutar

El user es PM (no dev) y depende de Claude para crecer técnicamente: *"no tengo
manera de capacitarme y aprendo si no es con vos"*. En CADA trabajo, además de
resolver lo pedido:

- **Detectar lo que él no sabe pedir**: joins innecesarios, queries ineficientes,
  colecciones mal modeladas, código que se puede simplificar, deuda técnica,
  riesgos de datos. Traerlo proactivamente aunque no lo haya pedido.
- **Enseñar el porqué**: explicar el concepto nuevo en lenguaje claro (gerencial
  + técnico), no solo aplicarlo. Que aprenda algo en cada interacción.
- **Proponer estructura nueva**, no solo optimizar lo existente al máximo. Leer
  como arquitecto SR: cuestionar el diseño de base.
- Esto NO reemplaza la REGLA #2 (no asumir, medir primero) ni el formato ejecutivo
  (REGLA #3). Va arriba de eso: hacer el trabajo Y dejar conocimiento.

Ver memorias [[feedback_proactive_architect]] y [[feedback_autonomy_lanes]].

## ⚠️ REGLA #8 — Portal INVITADO (www.acaquant.com): SOLO mercado, nunca filtrar datos privados

**Bloqueante. Es exposición de datos a gente externa a la empresa.** Conviven
dos portales:

- **trading.acaquant.com** — app interna de la mesa. Usuarios de la empresa
  (admin/trader/sales/etc.), ven todo según su rol.
- **www.acaquant.com** — **portal INVITADO**, lo usan **personas AJENAS a la
  empresa**. Es **EXCLUSIVO para datos de MERCADO** (home + módulos de mercado,
  read-only).

En CADA desarrollo, JAMÁS pasar por alto: cualquier cosa de trading que **no sea
home o mercados** (portfolios, operaciones, manager, back-office, acreencias,
gestión de ONs, clientes, AuM, P&L, contrapartes, segmentación, etc.) **NUNCA**
puede quedar accesible al invitado. Si un desarrollo nuevo no es de mercado, no
se mete en el portal www — punto.

- El backend fuerza rol `invitado` (default-deny) cuando ve el header
  `x-acaquant-portal: guest` (`api/auth.py::is_guest_portal` + check contra
  `core.roles.INVITADO_MODULES`). Agregar algo a `INVITADO_MODULES` es una
  decisión de SEGURIDAD — solo mercado.
- El frontend `acaquant-web` filtra nav/vistas por módulo; el invitado no debe
  ver ni el link de algo que no sea mercado.
- **Default-deny**: ante la duda, NO exponer al invitado.

Ver memoria [[feedback_portal_invitado_www]].

## Reglas que rompen todo si se olvidan

- **`python -m <módulo>` desde la raíz siempre**. `python engines/x.py` falla (`core` no es discoverable).
- **Nunca `client.close()` sobre los Mongo singletons** — mata el pool. Son 2: `core.mongo.get_mongo_client()` (rw, motores/crons) y `get_mongo_client_read()` (ro, `SECONDARY_PREFERRED`, usado por la API).
- **Regla de capas**: `core/` no importa nada del proyecto. `engines/` y `jobs/` usan `core/` + `quant/`. `api/services/` es puro (sin FastAPI), `api/routers/` solo HTTP plumbing.
- **Commits**: estilo `feat/fix/docs/refactor(scope): mensaje` en español, como el `git log`.
- **Constantes globales y feature flags** viven en `config.py` (raíz): `TICKERS_EXTRA_PRECIOS`, `TICKERS_BOOK_FULL`, etc. Env vars en `.env` local / systemd unit files en el Droplet (`MANAGER_EMAILS`, `DEFAULT_ROLE`, `MCP_*`, `MONGO_URI`).

> Validar imports antes de pushear router/service (REGLA #1) y la regla de
> services `@cached` → ver `api/CLAUDE.md`.

## Estructura

```
core/        # infra (mongo, mongo_monitor, postgres, grupos_sql, roles_sql, websocket, rofex_session, rofex_orders_session, roles, snapshot_writer, job_runs, profiler, byma, mae, cafci, finnhub, yahoo, openfigi, argentina_datos, dolar_oficial)
engines/     # motores WS → Mongo (always-on L-V 13-20 UTC) — incluye motor_cedears (alimenta Scanner CEDEARs)
jobs/        # batch/cron — incluye precios_acciones_daily (alimenta scanner via SQL mercado.precios_acciones)
quant/       # cálculo puro (black_scholes, stats, curve_fit, pivot_points, rolling_stats)
api/services # lógica pura (invocada por routers y por el agente)
api/routers  # thin HTTP wrappers. manager/ es paquete de sub-routers
api/mcp/     # MCP server (FastMCP) + OAuth 2.1 provider + discovery
partner_api/ # app FastAPI SEPARADA (no monta en api/main) — datos para proveedor externo
scripts/     # one-shot / migraciones / smoke
tests/       # pytest — unit/ + integration/ (marker `integration`, excluido por defecto via addopts)
sql/         # schema.sql — espejo relacional Postgres/Supabase (ver "Capa SQL")
deploy/      # systemd + crontab.txt (fuente de verdad)
.claude/     # settings.json + hooks + commands + skills + agents (ver .claude/INDEX.md)
docs/        # ARQUITECTURA.md (DOC MADRE: arquitectura/datos/estrategia/roadmap/plan SQL). Referencia operativa: API.md, MCP.md, MCP_TOOLS.md, MOTOR_VALUACIONES.md, RUNBOOK.md (operación/incidentes), SECRETS.md + SECURITY.md (seguridad), PARTNER_API*.md, TABLERO_COMERCIAL.md, GRUPOS.md, SEGMENTACION_PATRIMONIAL.md, HERRAMIENTAS.md (auto-gen). vault/ (cerebro Obsidian, auto-generado)
```

## Plano del sistema — `deploy/SISTEMA.md`

Fuente de verdad de TODO lo que corre: servicios systemd, motores, crons y
cómo se conectan. **Si agregás / quitás / modificás un servicio systemd o un
cron** (tocás `deploy/systemd/*.service` o `deploy/crontab.txt`), en el MISMO
cambio regenerá el plano:

```bash
python -m scripts.gen_sistema          # regenera las tablas (no editar a mano entre marcadores AUTOGEN)
python -m scripts.gen_sistema --check  # falla si SISTEMA.md quedó desincronizado
```

El inventario (servicios/motores/crons) es auto-generado desde la fuente
real → no puede mentir. La narrativa (topología, flujo de datos, bases) se
mantiene a mano. Si cambió cómo se conectan los servicios, actualizá esa
parte también. Skill: `/sistema`.

## Cerebro Obsidian — `docs/vault/`

Grafo navegable de TODO el sistema (módulos, componentes, rutas, crons,
colecciones, vistas, services, libs) como vault de Obsidian. El generador
`scripts/gen_obsidian.py` es **determinista**: parsea el código y reconstruye
archivos, links y backlinks de cada nota. La sección _Qué hace_ se completa con
una pasada de enriquecimiento con IA; el resto NO se edita a mano.

```bash
python -m scripts.gen_obsidian          # regenera las notas
python -m scripts.gen_obsidian --check  # CI: falla si el vault quedó stale
```

Mismo contrato que `gen_sistema`: tras un cambio estructural (router/cron/
colección/componente nuevo) el vault queda desincronizado → regenerar en el
mismo cambio. Cómo abrirlo: `docs/vault/README.md`.

## Comandos

```bash
uvicorn api.main:app --reload --port 8000
python -m engines.<motor> | jobs.<job> | scripts.<cmd>
ruff check . [--fix]                           # line-length=100, py312
pytest -ra                                     # unit (pyproject ya excluye integration via addopts)
pytest tests/<path>::<test_name>               # single test
pytest -m integration                          # integration (requiere Atlas up)
python -m scripts.perf_scan [--strict]         # anti-patterns Mongo
```

CI (`.github/workflows/ci.yml`): en cada push/PR a `main` corre `ruff check .` (bloqueante) + `perf_scan` (informativo, `continue-on-error`) + `pytest -ra` (solo unit). Python 3.12. No buildea el frontend.

```bash
uvicorn partner_api.main:app --port 8100   # Partner API (servicio externo, ver Estructura)
```

## Frontend en repo hermano

`../acaquant-web/` (Next.js 16, deploy auto a Vercel sobre `main` — `src/proxy.ts`, no middleware). **No es submodule** — es checkout paralelo. Cambios de API con impacto en UI se editan ahí con rutas absolutas (`C:\...\acaquant-web\...`). Las routes de Next que consumen endpoints "live fallback" necesitan `dynamic = "force-dynamic"` + `revalidate = 0` + `Cache-Control: no-store` (ver `api/CLAUDE.md`).

## Tablero Comercial (lente por operador)

`api/services/comercial.py` (selector por flag `COMERCIAL_SQL=1` → `api/services/comercial_sql.py`; el router `manager/comercial.py` fue eliminado). Cruza todo por `id_cuenta` (columna en SQL): QUIÉN (`clientes.comitentes` → operador + `nivel_1`), ACTIVIDAD (`operaciones.negocio_movimientos`/`operaciones.operaciones` → última op), TAMAÑO (`portafolio.tenencia`, `aum='si'`), operador↔usuario (`Manager.Users`, para cuentas huérfanas). Estado comercial por días desde última op: ACTIVA ≤45 / ENFRIANDOSE 45-90 / DORMIDA / NUEVA. Agrega EN VIVO con índices (el precompute `Clientes.ComercialCache` fue ELIMINADO — no se recrean rollups en SQL). Diseño: `docs/TABLERO_COMERCIAL.md`.

## Operaciones — SQL (migrado de Mongo 2026-06-16, CRÍTICO no inferible)

`operaciones.operaciones` (SQL Postgres) es la fuente de la vista MOVIMIENTOS (`/api/operaciones/ops/*`) + Contrapartes (`/operaciones/flujo`). **`CashFlow.Operaciones` (Mongo) y el rollup `CashFlow.OpsSerieDiaria` fueron ELIMINADOS** — ver `docs/SQL.md`. Origen: `jobs.operaciones_informes` (API informes Aunesa) que escribe SQL directo vía `operaciones_informes.ingestar_filas_sql` (normaliza + enriquece inline `moneda`/`mercado`/`operacion`/`nivel_3`/`segmento`/`es_cierre`/`commodity`/`mep`). `jobs.fci_bilateral` escribe el FCI bilateral (campo `etapa`) — upsert por boleto que NO pisa el resto. El catálogo `TiposOperacion` sigue en Mongo (chico).

**Las series se agregan EN VIVO desde SQL — NO hay rollup.** `/ops/serie` y `/ops/aranceles` agregan con `GROUP BY` + índices sobre `operaciones.operaciones` (`api/services/operaciones_sql.py`, flag `OPERACIONES_SQL=1`). El viejo `jobs/ops_rollup.py` + `OpsSerieDiaria` se mataron (no se recrean precomputes en SQL).

- **El arancel y el bruto NO comparten filtro de cierre**: para volumen `bruto` excluye `es_cierre=true`; para `arancel` se INCLUYEN los cierres (el **arancel de caución vive SOLO en el cierre**). `etapa <> 'solicitud'` siempre (la liquidación CL ya cuenta).
- **`es_cierre`** materializado (bool) separa volumen de arancel sin regex.
- El motor de PnL no usa esta tabla (cost-basis sale de `negocio_movimientos`); acá viven volumen/arancel comercial.
- Mismo patrón Mongo-rollup sigue SOLO para opciones (`jobs/options_rollup.py`, no migrado).

## Trading.Curvas — shape de flujos (CRÍTICO, no inferible)

- **CER**: porcentual. `amortizacion_pct` + `cupon_sobre_residual` YA resuelto (NO re-multiplicar por `residual_previo_pct`). `cupon_anual=0` si zero coupon. Requiere `cer_emision`.
- **tasa_fija**: absolutos. `amortizacion` + `interes`. Requiere `flujo_vencimiento`.
- **soberanos** (`tipo='globales'|'bonares'`): mismo shape que CER, `cupon_sobre_residual` ya en USD.

Agregar instrumento: doc en `Trading.Curvas` + fila en `portafolio.assets` (SQL) con `ticker == ticker_corto`. Sin el segundo no aparece en AuM/Portfolios. (El catálogo de títulos migró de Mongo `Valuaciones.Assets` a SQL `portafolio.assets` el 2026-06-15 — ver `docs/SQL.md`.)

`config.TICKERS_EXTRA_PRECIOS`: tickers que `motor_rofex` suscribe pero `motor_curvas` ignora. Default `['MERV - XMEV - AL30C - 24hs']` para `/api/analitica/canje`.

## Fórmulas no inferibles

**AuM / tenencias** — fuente única SQL `portafolio.tenencia` (writer diario
`jobs/portafolio_backfill --diario`, 11:00 UTC L-V). Mongo `Valuaciones.AuM` fue
**ELIMINADA** (2026-06-15) junto con `jobs/aum.py::run` (queda solo de librería de
helpers Aunesa) y la tabla SQL `aum`. El divisor de la valuación lo decide la
**CARTERA** (no más `tipoTitulo`, que se quedaba NULL):
- Renta fija (cartera `HD / DL / ARS`, cotiza en paridad) → `cantidad × precio / 100`
- Cash (`MONEDAS`), `FCI`, `RENTA VARIABLE` → `cantidad × precio` (NUNCA ÷100)
- Futuros (`DERIVADOS`) → `(precio + 1) × cantidad`
- El motor de PnL (`pnl.py::_aplicar_normalizer`) usa la MISMA regla por cartera.

**Breakevens** (`engines/breakevens.py`, método Buscar Objetivo, cupón cero):
```
retorno_lecap = flujo_vto_lecap / precio_lecap − 1
X = [(1 + retorno_lecap) · (precio_cer · cer_emision) / (vn_cer · cer_actual)]^(1/meses) − 1
```
Match **mismo vto** Lecap↔CER (`MAX_DIFF_DIAS=20`). Anualización con `dias_cer` = vto − 10 hábiles. Filtro `mes_inflacion ≤ último IPC publicado`. Fallback Fisher si faltan datos.

**Forwards**: `((1 + TEA_B)^t_B / (1 + TEA_A)^t_A)^(1/(t_B − t_A)) − 1`. Lee última TEA por ticker desde `MarketSnapshot.metrics.TEA` (escrita por `motor_curvas` en cada update). Igual patrón usan `breakevens.py` y los services de portfolio/renta-fija. **No leer TimeSales agregado** — es estrictamente más caro y devuelve el mismo valor que el snapshot live.

**TC Breakeven** (`api/services/renta_fija.py::_tc_breakeven`, sólo tasa fija nativa o CER fijado): `TC_BE = MEP × (flujo_vencimiento / precio_actual)`. Lee `flujo_vencimiento` de `Trading.Curvas`, `last_price` del trade más reciente y MEP de `get_ultimo_mep` (live, TTL 5s). Se calcula on-the-fly en `get_renta_fija` y `listar_curva` — no se persiste.

**AuM join chain**: `Trading.Curvas.curva` → `ticker_corto` → `portafolio.assets.ticker` → `unidad` → `portafolio.tenencia` (SQL, filtrar `aum='si'`).

**Enriquecimiento CER**: `motor_curvas` usa CER con settlement T-10 hábiles. Si un bono no opera un día, el último trade puede quedar con CER de ayer.

## Asistente legacy — ELIMINADO

`api/agent/` + `POST /api/chat` fueron **borrados del repo** (no existen más;
no documentar ni referenciar). El asistente con IA del producto es el MCP
server (sección siguiente).

## MCP server (Custom Connector)

`api/mcp/` montado en `https://api.acaquant.com/mcp` — 41 tools de SOLO LECTURA sobre datos de mercado (curvas, forwards, breakevens, opciones, REM, macro, descomposición, sensibilidad, order book live, renta variable / scanner CEDEARs / day-trading intradía). NO expone portfolio/operaciones/cuentas/AuM/manager (datos privados de la mesa). Cada tool es thin wrapper sobre `api/services/*`. Cliente principal: Claude Desktop / claude.ai vía Custom Connector. Doc completo de cada tool: `docs/MCP_TOOLS.md`.

**Auth**: OAuth 2.1 + PKCE + DCR (RFC 7591), Cloudflare Access como IdP. Flow completo en `docs/MCP.md`. Env vars: `MCP_BEARER_TOKEN` (static fallback dev/curl), `MCP_JWT_SECRET` (firma OAuth JWTs), `MCP_OAUTH_ISSUER` (default `https://api.acaquant.com`). Sin ninguno, `/mcp` queda deshabilitado.

**Dos cosas críticas que rompen el connector** (se aprendieron a los golpes; doc completo en memoria `project_mcp_cf_access.md`):

1. **CF Access path scoping**. App `acaquant-mcp-bypass` (BYPASS + Everyone) cubre 5 paths: `/mcp`, `/oauth/token`, `/oauth/register`, `/.well-known/oauth-protected-resource`, `/.well-known/oauth-authorization-server`. Si CF Access tapa `/mcp`, el cliente recibe HTML de login en vez de 401 → muere silencioso. `/oauth/authorize` SÍ debe estar protegido (ahí logea el user). 5/5 destinations al tope.
2. **`TransportSecuritySettings` en `api/mcp/server.py`** con `allowed_hosts` (`api.acaquant.com`) y `allowed_origins` (`https://claude.ai`, `https://claude.com`). El default del SDK MCP solo acepta localhost → 421 Misdirected Request. El smoke local NO replica esta condición.

## Partner API (servicio externo)

`partner_api/` es una **app FastAPI independiente** — NO se monta en `api/main`. Sirve datos de portfolio a un proveedor externo. En el Droplet corre como systemd `partner_api.service`, bindeado a `127.0.0.1:8100`, expuesto vía nginx en `data.acaquant.com`. Sin Swagger/OpenAPI público (`docs_url=None`); la doc va por escrito al proveedor (`docs/PARTNER_API.md`, `docs/PARTNER_API_PROVEEDOR.md`).

- Endpoints: `POST /v1/token` (login user/pass → JWT), `GET /v1/fechas`, `GET /v1/portfolio`, `GET /health`.
- DB propia: `ACAPortfolio.Cartera`. Env vars `PARTNER_MONGO_URI`, `PARTNER_JWT_SECRET` (chequeadas al importar `main.py`).
- Auth + rate limit propios (`partner_api/auth.py`, `security.py`, `ratelimit.py`) — no comparte código con `api/auth.py`.
- **Migrado a SQL (dual-run, 2026-06-23):** `ACAPortfolio.{Cartera,ApiUsers}` → schema `partner` (`partner.cartera`, `partner.api_users`). Lectura por flag `PARTNER_SQL=1` (default Mongo) vía `partner_api/store.py`; escritura por flag `PARTNER_SQL_WRITE=1` (dual-write best-effort) en `jobs/partner_export.py` + `scripts/partner_user.py`. Conexión propia `partner_api/pg.py` (no `core.postgres`). Baseline: `scripts/partner_sql_baseline.py`. Ver `docs/PARTNER_API.md` y `docs/SQL.md`.

## Capa SQL — Postgres/Supabase (migración Mongo→PG en curso)

**Postgres NO reemplaza Mongo: es un espejo relacional de solo-lectura** del
núcleo de negocio (reportería con SQL real, cruces baratos). Si PG se cae, la
operación (Mongo) sigue. Doc completo y estado por fase: **`docs/SQL.md`** +
`docs/MIGRACION_MONGO_SUPABASE.md`. Esquema: `sql/schema.sql`. Pool/conn:
`core.postgres.get_pool` (lee `.env` propia). Sync: `jobs/sync_postgres.py`.

- **Patrón dual-run (no inferible)**: los endpoints migrados leen SQL **o** Mongo
  según un flag, con el path Mongo intacto → rollback = sacar la env + restart.
  Primer feature migrado: vista OPERACIONES (`/api/operaciones/ops/*`) vía
  `api/services/operaciones_sql.py`, flag global `OPERACIONES_SQL=1` (override por
  request `?_engine=sql|mongo`), selector en `operaciones.py::_motor()`.
- **GATE antes de cutover**: correr el comparador SQL↔Mongo (ej.
  `scripts/compare_ops_sql_vs_mongo.py`) y exigir paridad total. NO migrar lecturas
  a ciegas — las reglas de traducción Mongo→SQL son sutiles (NULL vs `''`, `es_cierre`,
  `etapa`, `ABS`/`COALESCE`); están documentadas en `docs/SQL.md`.
- Otros módulos SQL ya escritos: `core/grupos_sql.py`, `core/roles_sql.py`. Estado de
  qué dominio lee SQL vs Mongo: `python -m scripts.estado_sql`.

## Deploy

Push a `main` → Vercel auto-deploya acaquant-web. Backend: `git pull` + `systemctl restart api.service` en el Droplet, o skill `/deploy`. Motores de mercado los controla cron (start/stop L-V). Cron fuente de verdad: `deploy/crontab.txt`.

> **Las colecciones espejo `*API` fueron ELIMINADAS (2026-06-06).** La API lee
> las fuentes directo (`CashFlow.*`) y, para tenencias/catálogo, **SQL**
> `portafolio.tenencia` / `portafolio.assets` (Mongo `Valuaciones.AuM/Assets`
> eliminadas 2026-06-15). El join Curvas+BondsMaster y la normalización de Assets
> los hace `api/services/titulos_flujos.py` (lee `portafolio.assets`). Ya NO existen
> `jobs/sync_api_copies.py` ni `scripts/api_migrate.py`.

Jobs críticos diarios: `jobs.bcra --today` (22 UTC L-V, pide hoy+21d para CER forward), `jobs.argentina_datos` (12 UTC, RiesgoPais/IPC/REM), `jobs.portafolio_backfill --diario` (11 UTC L-V, writer de tenencias SQL — reemplazó a `jobs.aum`/Mongo, eliminado), `jobs.cleanup_curvas` + `jobs.cleanup_futuros_dlr` (12:30 UTC L-V, antes de motores), `jobs.snapshot_cierre` (20:25 UTC L-V, post-cierre — lee `MarketSnapshot` y persiste cierre por bono en `Trading.SnapshotsCierre`), `jobs.negocio_movimientos` (cada hora 15-22 UTC L-V, pega a Aunesa `consolidadosGenerales`, parsea/categoriza/agrupa por boleto y persiste idempotente en **SQL `operaciones.negocio_movimientos`** para la vista `/operaciones/negocio`).

Dólar oficial: única fuente live es `Valuaciones.DolarOficialLive` (feed MAE mayorista UST$T plazo 000, script local en PC oficina). Histórico/anchors (7d/MTD/YTD del watchlist `/argy`) deshabilitado hasta que MAE acumule histórico suficiente. Para series macro (`serie_macro` con `dolar_oficial`/`dolar_mayorista`) usar `Trading.DOLAR` (BCRA A3500 fixing diario).
