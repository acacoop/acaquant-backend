# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

TradingAV — plataforma quant MERVAL/ROFEX. pyRofex WS → MongoDB Atlas M10 → FastAPI (`api.acaquant.com`) → **acaquant-web** Next.js en Vercel (`trading.acaquant.com`). Server en `/root/TradingAV` (Droplet DO), venv en `/root/TradingAV/venv`.

**DBs Mongo**: `Trading` (Curvas, MarketSnapshot, SnapshotsCierre, CanjeCierre, OrderBookL2, TimeSales, DOLAR), `Valuaciones` (Assets, AuM, DolarOficialLive, PnLTotalesCache, ConsolidadoCuentas), `CashFlow` (Contrapartes, Productores, NegocioMovimientos), `Manager` (Users, RoleMatrix, RoleAudit), `Clientes` (Comitentes, ComercialCache — segmentación/operador asignado), `CuentasAPI` (AccionistasAPI, ContrapartesAPI — copias derivadas), `MCP` (OAuth codes/tokens, TTL automático).

## Contexto por subdirectorio

Cada carpeta grande tiene su propio `CLAUDE.md` con lo que aplica SOLO ahí —
se carga automáticamente al trabajar en esa carpeta. Este archivo (raíz)
tiene lo que aplica a todo el repo.

- **`api/CLAUDE.md`** — ⚠️ REGLA #1 (validar imports), RBAC, services `@cached`, filtros de cuenta, live fallback, motor de PnL.
- **`engines/CLAUDE.md`** — patrón de escritura a `MarketSnapshot`, Atlas / motores stale.
- **`jobs/CLAUDE.md`** — filtros de exclusión del AuM, encadenado `sync_api_copies`.
- **`scripts/CLAUDE.md`** — REGLA #0 aplicada, patrón de backfill multi-mes.

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
jobs/        # batch/cron — incluye precios_acciones_daily (alimenta scanner via Trading.PreciosAcciones TS)
quant/       # cálculo puro (black_scholes, stats, curve_fit, pivot_points, rolling_stats)
api/services # lógica pura (invocada por routers y por el agente)
api/routers  # thin HTTP wrappers. manager/ es paquete de sub-routers
api/agent/   # asistente tool-use (LEGACY, no en uso — ver sección "Asistente")
api/mcp/     # MCP server (FastMCP) + OAuth 2.1 provider + discovery
partner_api/ # app FastAPI SEPARADA (no monta en api/main) — datos para proveedor externo
scripts/     # one-shot / migraciones / smoke
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

`../acaquant-web/` (Next.js 15, deploy auto a Vercel sobre `main`). **No es submodule** — es checkout paralelo. Cambios de API con impacto en UI se editan ahí con rutas absolutas (`C:\...\acaquant-web\...`). Las routes de Next que consumen endpoints "live fallback" necesitan `dynamic = "force-dynamic"` + `revalidate = 0` + `Cache-Control: no-store` (ver `api/CLAUDE.md`).

## Tablero Comercial (lente por operador)

`api/services/comercial.py` + `api/routers/manager/comercial.py` (solo manager). Cruza todo por `id_cuenta` (denormalizado e indexado en `NegocioMovimientos` — **nunca regex sobre `cuenta`**; backfill viejo: `scripts/backfill_id_cuenta_negocio.py`): QUIÉN (`Clientes.Comitentes` → operador + `nivel_1`), ACTIVIDAD (`CashFlow.NegocioMovimientos` → última op), TAMAÑO (`Valuaciones.AuM`), operador↔usuario (`Manager.Users`, para cuentas huérfanas). Estado comercial por días desde última op: ACTIVA ≤45 / ENFRIANDOSE 45-90 / DORMIDA / NUEVA. Cacheado on-the-fly (TTL); si pesa, mover a precompute `Clientes.ComercialCache`. Diseño: `docs/TABLERO_COMERCIAL.md`.

## Operaciones — rollup, NO escanear (CRÍTICO, no inferible)

`CashFlow.Operaciones` (~487k docs / ~213MB) es la fuente de la vista MOVIMIENTOS (`api/routers/operaciones.py`, `/api/operaciones/ops/*`). Origen: `jobs.operaciones_informes` (API informes), enriquecida con `moneda`/`mercado`/`operacion`/`nivel_3`/`segmento` por `scripts/enrich_operaciones.py` (+ backfills `backfill_nivel3_operaciones.py`, `backfill_es_cierre_operaciones.py`, `backfill_commodity_operaciones.py`).

**Regla de oro: NUNCA re-agregar toda la historia de Operaciones por request.** Las series (`/ops/serie`, `/ops/aranceles`) escaneaban ~200k docs SIN filtro de fecha → desalojaban el cache del M10 → CPU. Ahora leen el rollup pre-agregado `CashFlow.OpsSerieDiaria` (`jobs/ops_rollup.py`, cron `40 13-22 * * 1-5`).

- **Grano del rollup** (1 doc por combo): `{fecha, moneda, mercado, operacion, segmento, nivel_3} → {bruto, arancel, n}`. `etapa≠solicitud` siempre. **El arancel y el bruto NO comparten filtro de cierre**: `bruto` excluye `es_cierre=True` (repetiría el nocional → se fuerza a 0); `arancel` INCLUYE los cierres con arancel porque el **arancel de caución vive SOLO en el cierre** (`es_cierre=True`; ver `diag_aranceles_caucion`). `arancel` se guarda en valor **absoluto** (`$abs`). En el endpoint, volumen usa `_ops_match` (es_cierre=False), arancel usa `_arancel_match` (incluye cierres con arancel).
- **NO incluye `denominacion`/`cuenta`** (alta cardinalidad). Esos filtros + el scope de cuenta caen a query **live** en el endpoint (ya usan índice).
- **Live-fallback**: el rollup cubre hasta AYER; el día de HOY se agrega en vivo (1 día → índice `concertacion`, barato) y se mergea. Si el rollup está vacío para esa moneda (no construido), el helper devuelve `None` → el caller cae a la query live completa. Ver `_serie_bruto_rollup` en `operaciones.py`.
- **Cierre de caución**: el campo materializado `es_cierre` (indexable, NO `$not /Cierre/` que forzaba COLLSCAN) separa volumen de arancel. Para **volumen** se excluye (el cierre repite el nocional); para **arancel** se INCLUYE (es donde está el fee de caución — las aperturas no traen arancel). `etapa=solicitud` no suma en ningún caso (la liquidación CL ya cuenta).
- **Modos del job**: incremental (recalcula últimos 7 días — boletos retro) por default; `--full` rebuild completo con swap atómico (`reemplazar_coleccion_atomico`, sin ventana de vacío). Backfill inicial: `python -m jobs.ops_rollup --full`.
- Mismo patrón rollup-no-escanear para opciones: `jobs/options_rollup.py`.

## Trading.Curvas — shape de flujos (CRÍTICO, no inferible)

- **CER**: porcentual. `amortizacion_pct` + `cupon_sobre_residual` YA resuelto (NO re-multiplicar por `residual_previo_pct`). `cupon_anual=0` si zero coupon. Requiere `cer_emision`.
- **tasa_fija**: absolutos. `amortizacion` + `interes`. Requiere `flujo_vencimiento`.
- **soberanos** (`tipo='globales'|'bonares'`): mismo shape que CER, `cupon_sobre_residual` ya en USD.

Agregar instrumento: doc en `Trading.Curvas` + doc en `Valuaciones.Assets` con `TICKER == ticker_corto`. Sin el segundo no aparece en AuM/Portfolios.

`config.TICKERS_EXTRA_PRECIOS`: tickers que `motor_rofex` suscribe pero `motor_curvas` ignora. Default `['MERV - XMEV - AL30C - 24hs']` para `/api/analitica/canje`.

## Fórmulas no inferibles

**AuM** (`jobs/aum.py`, `api/services/portfolio.py::_valuacion_api`):
- Renta fija (`Títulos Públicos, Letras, ONs, Fideicomisos, CPD`) → `cantidad × precio / 100`
- FCI / OTROS → `cantidad × precio`
- Futuros → `(precio + 1) × cantidad`

**Breakevens** (`engines/breakevens.py`, método Buscar Objetivo, cupón cero):
```
retorno_lecap = flujo_vto_lecap / precio_lecap − 1
X = [(1 + retorno_lecap) · (precio_cer · cer_emision) / (vn_cer · cer_actual)]^(1/meses) − 1
```
Match **mismo vto** Lecap↔CER (`MAX_DIFF_DIAS=20`). Anualización con `dias_cer` = vto − 10 hábiles. Filtro `mes_inflacion ≤ último IPC publicado`. Fallback Fisher si faltan datos.

**Forwards**: `((1 + TEA_B)^t_B / (1 + TEA_A)^t_A)^(1/(t_B − t_A)) − 1`. Lee última TEA por ticker desde `MarketSnapshot.metrics.TEA` (escrita por `motor_curvas` en cada update). Igual patrón usan `breakevens.py` y los services de portfolio/renta-fija. **No leer TimeSales agregado** — es estrictamente más caro y devuelve el mismo valor que el snapshot live.

**TC Breakeven** (`api/services/renta_fija.py::_tc_breakeven`, sólo tasa fija nativa o CER fijado): `TC_BE = MEP × (flujo_vencimiento / precio_actual)`. Lee `flujo_vencimiento` de `Trading.Curvas`, `last_price` del trade más reciente y MEP de `get_ultimo_mep` (live, TTL 5s). Se calcula on-the-fly en `get_renta_fija` y `listar_curva` — no se persiste.

**AuM join chain**: `Trading.Curvas.curva` → `ticker_corto` → `Valuaciones.Assets.TICKER` → `unidad` → `Valuaciones.AuM`.

**Enriquecimiento CER**: `motor_curvas` usa CER con settlement T-10 hábiles. Si un bono no opera un día, el último trade puede quedar con CER de ayer.

## Asistente (legacy, no en uso)

`api/agent/` + `POST /api/chat` siguen en el repo como referencia (provider Claude con router Haiku/Sonnet, tools sobre `api/services/*`, `BLOCKED_PATH_PREFIXES` policy). **No se está usando** en producción — no modificar ni invertir tiempo sin coordinar primero. Ningún flow del producto lo invoca.

## MCP server (Custom Connector)

`api/mcp/` montado en `https://api.acaquant.com/mcp` — 39 tools de SOLO LECTURA sobre datos de mercado (curvas, forwards, breakevens, opciones, REM, macro, descomposición, sensibilidad, order book live, renta variable / scanner CEDEARs). NO expone portfolio/operaciones/cuentas/AuM/manager (datos privados de la mesa). Cada tool es thin wrapper sobre `api/services/*`. Cliente principal: Claude Desktop / claude.ai vía Custom Connector. Doc completo de cada tool: `docs/MCP_TOOLS.md`.

**Auth**: OAuth 2.1 + PKCE + DCR (RFC 7591), Cloudflare Access como IdP. Flow completo en `docs/MCP.md`. Env vars: `MCP_BEARER_TOKEN` (static fallback dev/curl), `MCP_JWT_SECRET` (firma OAuth JWTs), `MCP_OAUTH_ISSUER` (default `https://api.acaquant.com`). Sin ninguno, `/mcp` queda deshabilitado.

**Dos cosas críticas que rompen el connector** (se aprendieron a los golpes; doc completo en memoria `project_mcp_cf_access.md`):

1. **CF Access path scoping**. App `acaquant-mcp-bypass` (BYPASS + Everyone) cubre 5 paths: `/mcp`, `/oauth/token`, `/oauth/register`, `/.well-known/oauth-protected-resource`, `/.well-known/oauth-authorization-server`. Si CF Access tapa `/mcp`, el cliente recibe HTML de login en vez de 401 → muere silencioso. `/oauth/authorize` SÍ debe estar protegido (ahí logea el user). 5/5 destinations al tope.
2. **`TransportSecuritySettings` en `api/mcp/server.py`** con `allowed_hosts` (`api.acaquant.com`) y `allowed_origins` (`https://claude.ai`, `https://claude.com`). El default del SDK MCP solo acepta localhost → 421 Misdirected Request. El smoke local NO replica esta condición.

## Partner API (servicio externo)

`partner_api/` es una **app FastAPI independiente** — NO se monta en `api/main`. Sirve datos de portfolio a un proveedor externo. En el Droplet corre como systemd `partner_api.service`, bindeado a `127.0.0.1:8100`, expuesto vía nginx en `data.acaquant.com`. Sin Swagger/OpenAPI público (`docs_url=None`); la doc va por escrito al proveedor (`docs/PARTNER_API.md`, `docs/PARTNER_API_PROVEEDOR.md`).

- Endpoints: `POST /v1/token` (login user/pass → JWT), `GET /v1/fechas`, `GET /v1/portfolio`, `GET /health`.
- DB propia: `ACAPortfolio.Cartera`. Env vars `PARTNER_MONGO_URI`, `PARTNER_JWT_SECRET` (chequeadas al importar `main.py`).
- Auth + rate limit propios (`partner_api/auth.py`, `security.py`, `ratelimit.py`) — no comparte código con `api/auth.py`.

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
> las fuentes directo (`CashFlow.*`, `Valuaciones.AuM/Assets`) y, para el join
> Curvas+BondsMaster y la normalización de Assets, usa el servicio
> `api/services/titulos_flujos.py`. Ya NO existen `jobs/sync_api_copies.py` ni
> `scripts/api_migrate.py`. Ver memoria `project_api_migrations` (obsoleta).

Jobs críticos diarios: `jobs.bcra --today` (22 UTC L-V, pide hoy+21d para CER forward), `jobs.argentina_datos` (12 UTC, RiesgoPais/IPC/REM), `jobs.aum` (23 L-V), `jobs.cleanup_curvas` + `jobs.cleanup_futuros_dlr` (12:30 UTC L-V, antes de motores), `jobs.snapshot_cierre` (20:25 UTC L-V, post-cierre — lee `MarketSnapshot` y persiste cierre por bono en `Trading.SnapshotsCierre`), `jobs.negocio_movimientos` (cada hora 15-22 UTC L-V, pega a Aunesa `consolidadosGenerales`, parsea/categoriza/agrupa por boleto y persiste idempotente en `CashFlow.NegocioMovimientos` para la vista `/operaciones/negocio`).

Dólar oficial: única fuente live es `Valuaciones.DolarOficialLive` (feed MAE mayorista UST$T plazo 000, script local en PC oficina). Histórico/anchors (7d/MTD/YTD del watchlist `/argy`) deshabilitado hasta que MAE acumule histórico suficiente. Para series macro (`serie_macro` con `dolar_oficial`/`dolar_mayorista`) usar `Trading.DOLAR` (BCRA A3500 fixing diario).
