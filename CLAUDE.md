# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TradingAV es la plataforma cuantitativa de mercados argentinos (MERVAL/ROFEX). Streama datos en tiempo real, corre motores analíticos paralelos (microstructure, opciones, curvas, forwards, breakevens), persiste en MongoDB Atlas (cluster M10), y expone datos vía REST API (FastAPI) consumida por **acaquant-web** (Next.js), el frontend activo en `trading.acaquant.com`.

## Acceso

| Capa | URL | Auth |
|---|---|---|
| Frontend (acaquant-web) | trading.acaquant.com | Cloudflare Access (email OTP) |
| REST API | api.acaquant.com | Bearer `API_KEY` + Cloudflare Access service token |
| Swagger UI | api.acaquant.com/docs | — |

**Infraestructura backend**: Droplet DigitalOcean. `cloudflared.service` (always-on) establece el tunnel hacia Cloudflare. Cloudflare Access exige OTP antes de llegar al servidor.
**Infraestructura frontend**: acaquant-web deploya en **Vercel** (push a `main` → deploy automático). No tiene systemd ni Droplet.

## Variables de entorno requeridas (`.env`)

```
ROFEX_USER / ROFEX_PASSWORD / ROFEX_ACCOUNT / ROFEX_API_URL / ROFEX_WS_URL
MONGO_URI          ← usuario read-write (motores + Manager)
MONGO_URI_READ     ← usuario read-only (API)
AUNESA_CLIENT_ID / AUNESA_USERNAME / AUNESA_PASSWORD
MANAGER_EMAILS     ← emails separados por coma con acceso Manager/Asistente
API_KEY            ← Bearer para autenticar requests a la API (vacío = dev)
ANTHROPIC_API_KEY  ← Claude (default)
GEMINI_API_KEY     ← Gemini Flash (fallback legacy + intel_extraction)
LLM_PROVIDER       ← claude | gemini (default: claude)
FINNHUB_API_KEY    ← news globales + calendario económico
CF_ACCESS_TEAM / CF_ACCESS_AUD             ← validación JWT CF Access
CF_TRUSTED_SERVICE_TOKENS                   ← common_names allow-list (acaquant-web SSR)
BYMA_CLIENT_ID / BYMA_CLIENT_SECRET         ← OAuth2 BYMA Primarias
BYMA_TOKEN_URL / BYMA_BASE_URL              ← endpoints BYMA (defaults en config.py)
ATLAS_PUBLIC_KEY / ATLAS_PRIVATE_KEY / ATLAS_PROJECT_ID / ATLAS_CLUSTER_NAME  ← pausa nocturna Atlas
```

## Estructura de carpetas

```
TradingAV/
├── config.py                 # credenciales .env
│
├── core/                     # infra compartida (importada por todos)
│   ├── mongo.py              # singletons get_mongo_client() / get_mongo_client_read()
│   ├── mongo_monitor.py      # command listener para métricas Atlas
│   ├── profiler.py           # Stopwatch (traces de latencia)
│   ├── rofex_session.py      # auth pyRofex
│   ├── websocket.py          # WebSocketManager
│   ├── snapshot_writer.py    # writer background genérico
│   ├── yahoo.py / finnhub.py # clientes externos (quotes, news, calendar)
│   ├── job_runs.py           # JobRunLogger context manager → Manager.JobRuns
│   └── byma.py               # cliente OAuth2 BYMA Primarias Placements
│
├── engines/                  # motores always-on (WS → Mongo)
│   ├── _curvas_loader.py     # helper compartido para leer Trading.Curvas
│   ├── valores.py            # Trading.TimeSales + MarketSnapshot
│   ├── curvas.py             # enriquecimiento TEA/Duration/Convexity
│   ├── options.py            # Opciones.OptionsSnapshot (incluye MongoManager)
│   ├── forwards.py           # Trading.ForwardsLive + Historico
│   ├── breakevens.py         # Trading.BreakevensLive + Historico
│   ├── caucion.py            # Trading.CaucionSnapshot (ARS+USD, plazo dinámico)
│   ├── futuros_dlr.py        # Trading.FuturosDLRSnapshot (curva outrights)
│   ├── dolares.py            # Valuaciones.DolarSnapshot (MEP/CCL/canje live)
│   └── dolar_mep.py          # cron intradía que escribe Valuaciones.Dolar (histórico)
│
├── jobs/                     # batch/cron (sin WebSocket)
│   ├── aunesa_client.py      # cliente API Aunesa
│   ├── carteras.py           # Aunesa → Valuaciones.Carteras
│   ├── aum.py                # snapshot AuM diario + sync CarterasII
│   ├── aum_backfill.py       # reconstrucción histórica
│   ├── aum_resumen_fci.py    # rollup 1 doc/fecha → Valuaciones.AuMResumenFCI
│   ├── cleanup_curvas.py     # elimina instrumentos vencidos de Trading.Curvas
│   ├── cashflow.py           # movimientos → CashFlow.Movimientos
│   ├── flujo_contrapartes.py # operaciones del día → CashFlow.Flujo
│   ├── segmento_contrapartes.py  # setea Fondos/ALYC/Bancos
│   ├── volatilidad_ggal.py   # VR histórica GGAL al cierre
│   ├── options_rollup.py     # rollup Opciones.Data → DataHistorica
│   ├── bcra.py               # CER/TAMAR/DOLAR/BADLAR
│   ├── sync_api_copies.py    # re-sync colecciones *API.*API desde fuentes
│   ├── dias_habiles.py       # calendario hábil argentino
│   ├── news_ingesta.py       # RSS + Finnhub global → Manager.News
│   ├── news_finnhub.py       # ingesta news Finnhub
│   ├── economic_calendar.py  # Finnhub economic calendar
│   ├── market_quotes.py      # watchlist equity + Treasuries (yfinance)
│   └── market_anchors.py     # anchors 7d/MTD/YTD/1Y + retornos
│
├── quant/                    # cálculo puro (sin I/O de red)
│   ├── black_scholes.py      # bs_price / bs_delta / bs_gamma / bs_vega / bs_theta / find_iv
│   └── stats.py              # percentile, zscore, classify_level (benchmarks dinámicos)
│
├── api/                      # REST API (FastAPI) — consumida por acaquant-web
│   ├── main.py               # entrypoint FastAPI (lifespan + middlewares)
│   ├── auth.py               # get_user_email + require_manager (JWT CF Access)
│   ├── ratelimit.py          # Limiter compartido slowapi (key por identidad)
│   ├── cache.py              # @cached(ttl) in-process (negative caching off)
│   ├── db.py                 # get_db_* (sin fastapi, usable por services)
│   ├── deps.py               # verify_api_key + re-export de db helpers
│   ├── services/             # lógica pura (sin FastAPI)
│   │   ├── cotizaciones.py   # listar_curva, get_caucion, get_futuros_dlr,
│   │   │                       snapshot_curva_historico, calcular_pendiente_curva,
│   │   │                       liquidez_secundario, etc.
│   │   ├── macro.py          # obtener_serie_macro, clasificar_nivel
│   │   └── argy.py           # panel ARGY (MEP/CCL/canje/caución con returns)
│   ├── agent/                # asistente IA (tool-use Claude/Gemini)
│   │   ├── provider.py       # ClaudeProvider + GeminiProvider
│   │   ├── router.py         # decide_model (haiku/sonnet por heurísticas)
│   │   ├── tools.py          # menú de tools + dispatch + BLOCKED_PATH_PREFIXES
│   │   ├── service_registry.py # endpoint → función (sin HTTP loopback)
│   │   ├── prompt.py         # system prompt + caching ephemeral
│   │   ├── context.py        # foto del día + último IntelDoc
│   │   ├── runner.py         # bucle tool-use (MAX_STEPS=6)
│   │   └── invariants.py     # checks dominio (paridad, convexity, duration)
│   └── routers/
│       ├── analitica.py      # /api/analitica/* (Tier 1 + Tier 2 tools)
│       ├── carteras.py       # /api/portfolio/*
│       ├── chat.py           # /api/chat (asistente de mesa)
│       ├── cotizaciones.py   # /api/cotizaciones/* (incluye caución, futuros DLR, ARGY)
│       ├── cuentas.py        # /api/cuentas/*
│       ├── manager.py        # /api/manager/* (status, jobs, checks, latencia, intel)
│       ├── manager_resources.py # /api/manager/resources (sampler CPU/RAM)
│       ├── market.py         # /api/market/* (watchlist equity + Treasuries)
│       ├── news.py           # /api/news (feed Bloomberg-style)
│       ├── operaciones.py    # /api/operaciones/*
│       └── titulos.py        # /api/titulos/*
│
├── scripts/                  # one-shot / diagnóstico manual
│   ├── crear_indices.py      # idempotente
│   ├── api_migrate.py        # migraciones colecciones legacy → API
│   ├── test_api.py           # smoke test endpoints API
│   ├── test_byma.py          # smoke test BYMA Primarias (4 endpoints)
│   ├── perf_scan.py          # análisis estático anti-patterns Mongo
│   └── check_*.py / debug_*.py
│
├── tests/                    # pytest unit tests (no requieren Mongo)
│   └── unit/                 # black_scholes, breakevens, curvas, dias_habiles, etc.
│
├── deploy/
│   ├── systemd/              # .service files (motor_* + api + cloudflared)
│   └── crontab.txt           # fuente de verdad del cron
│
└── docs/                     # API.md, API_MIGRATIONS.md, ASISTENTE.md, asistente/estrategia.md, asistente/estrategias.md
```

**Regla de capas**: `core/` no importa a nadie. `engines/` y `jobs/` importan `core/` + `quant/`. `api/` usa `core.mongo.get_mongo_client_read()` (read-only). `scripts/` puede importar lo que necesite.

## Frontend: acaquant-web

Repo separado (ubicación local varía por máquina; sibling del repo `TradingAV`). **Este es el frontend activo.**

**Stack**: Next.js 15 (App Router) + TypeScript + Tailwind CSS 4 + React 19. Lightweight Charts para gráficos trading-style, Recharts para el resto.

### Estructura acaquant-web

```
src/
├── app/
│   ├── layout.tsx            # Root layout: Header, TopTicker, AutoRefresh
│   ├── page.tsx              # HOME con news panel live
│   ├── renta-fija/page.tsx   # RENTA FIJA: cotizaciones, curvas, forwards, breakevens
│   ├── /api/                 # Proxy routes (ocultan API_KEY del cliente)
│   │   ├── aum-fci/snapshot + serie
│   │   ├── cashflow, contrapartes, flujo-vs-aum
│   │   ├── historico-curva, opciones-meta, trades
│   ├── /aum /derivados /operaciones /portfolios /retorno
├── components/               # Componentes por dominio (tabla renta fija, curvas, libro, etc.)
├── lib/
│   ├── api.ts                # apiFetch(): Bearer token + CF service token + ISR TTL
│   └── estrategias.ts        # lógica de estrategias opciones (pura)
```

### Patrón API proxy

Las API routes de Next.js (`src/app/api/*/route.ts`) proxean a `api.acaquant.com` para no exponer el `API_KEY` al browser. `apiFetch()` en `lib/api.ts` maneja:
- Header `Authorization: Bearer <API_KEY>`
- Headers `CF-Access-Client-Id` / `CF-Access-Client-Secret` (service token para bypass Cloudflare Access)
- `next: { revalidate: N }` por ruta (ISR, TTL variable)

Las páginas son **server components async** con `Promise.all()` para fetching paralelo. `safeFetch()` envuelve cada llamada con fallback para graceful degradation si el backend no responde.

### Gating admin — proxy.ts (Next 16)

**Importante Next 16+**: el viejo `middleware.ts` fue renombrado a **`src/proxy.ts`** (exporta función `proxy`, no `middleware`). Tener los dos archivos a la vez rompe el build. Solo se usa `proxy.ts`.

`src/proxy.ts` restringe `/manager`, `/asistente` y `/api/chat` a los emails de `MANAGER_EMAILS`. El email viene del header `cf-access-authenticated-user-email` que inyecta Cloudflare Access. Si `MANAGER_EMAILS` vacío → modo dev, deja pasar todo.

`layout.tsx` además lee el header server-side para computar `isManager` y pasárselo a `<Header />`, que filtra los links admin del nav.

### Variables de entorno acaquant-web (`.env.local`)

```
API_URL                    ← URL base de la FastAPI (https://api.acaquant.com)
API_KEY                    ← Bearer token
CF_ACCESS_CLIENT_ID        ← service token para bypass Cloudflare Access
CF_ACCESS_CLIENT_SECRET
```

### Comandos acaquant-web

```bash
npm run dev      # localhost:3000
npm run build
npm run lint     # ESLint
```

## Running the Project (TradingAV)

Todo se ejecuta desde la raíz del proyecto con `python -m <módulo>`:

```bash
pip install -r requirements.txt

# REST API
uvicorn api.main:app --reload --port 8000

# Motores (systemd los corre como `python -m engines.<nombre>`)
python -m engines.valores
python -m engines.options
python -m engines.curvas
python -m engines.forwards
python -m engines.breakevens
python -m engines.dolar_mep

# Jobs batch
python -m jobs.aum                     # snapshot AuM (cron 23:00 UTC) + sync CarterasII
python -m jobs.aum_resumen_fci
python -m jobs.carteras
python -m jobs.cleanup_curvas          # --dry para preview
python -m jobs.cashflow --today
python -m jobs.bcra --today

# Migraciones API
python -m scripts.api_migrate <comando>   # accionistas, contrapartes, flujo, movimientos, carteras, aum, assets, flujos-titulos

# Test y lint
ruff check .
ruff check . --fix
pytest -ra                                        # todos los unit tests
pytest tests/unit/test_black_scholes.py           # un archivo
pytest -m integration                             # requiere Mongo (excluidos por defecto)
python -m scripts.perf_scan                       # anti-patterns Mongo (informativo)
python -m scripts.perf_scan --strict              # exit 1 si hay findings
python -m scripts.test_api                        # smoke test endpoints (localhost:8000)
python -m scripts.test_gemini                     # smoke test Gemini API (ambos modelos)
python -m scripts.test_chat                       # smoke test /api/chat (requiere uvicorn up)
python -m scripts.crear_indices                   # idempotente
```

CI (`.github/workflows/ci.yml`) corre `ruff check`, `perf_scan` (informativo) y `pytest -ra` en cada push a `main`.

**ruff** (`pyproject.toml`): `line-length=100`, `target-version=py312`. Ignora `E501` (pipelines Mongo largos). `scripts/*` permite `print`. `# noqa: PERF001` suprime findings de perf_scan línea a línea.

## Architecture

```
ROFEX WebSocket (pyRofex)
        │
        ▼
core.websocket.WebSocketManager
  - Suscribe tickers en chunks de 50
  - Dispatches a handlers por motor
        │
   ┌────┴────┬──────────┐
   ▼         ▼          ▼
engines.valores  engines.options  engines.curvas  ...
        │
        ▼
MongoDB Atlas M10 (Trading, Opciones, Valuaciones, CashFlow)
        │
        ▼
FastAPI (api.acaquant.com)
        │
        ▼
acaquant-web Next.js (trading.acaquant.com)
```

### Key Components

- **`config.py`** — Config centralizado; carga `.env`.
- **`core/rofex_session.py`** — Auth única de pyRofex (`inicializar_sesion`).
- **`core/websocket.py`** — `WebSocketManager`: suscripciones WS; registra handlers `update_price(ticker, data)` por motor.
- **`core/mongo.py`** — Dos clientes singleton thread-safe (double-checked locking). `serverSelectionTimeoutMS=30000`, `maxPoolSize=20`, `compressors="zstd,snappy,zlib"`:
  - `get_mongo_client()` → `MONGO_URI` (read-write). Motores, crons.
  - `get_mongo_client_read()` → `MONGO_URI_READ` con `read_preference=SECONDARY_PREFERRED`. Usado por la API. Fallback a `MONGO_URI` si no está definido.
  - **Nunca llamar `client.close()`** — son singletons de larga vida.

### Trading Engines

Cada motor tiene `update_price(ticker, data)` llamado por el WebSocket en cada tick.

| Motor | Colección MongoDB | Descripción |
|---|---|---|
| `engines/valores.py` | `Trading.TimeSales` + `Trading.MarketSnapshot` | Microestructura bonos/Lecaps/CER: inserta trades en TimeSales, snapshot cada 1s en MarketSnapshot |
| `engines/options.py` | `Opciones.OptionsSnapshot` | Opciones GGAL: Black-Scholes Greeks, IV via Newton-Raphson. Contiene la clase local `MongoManager` |
| `engines/curvas.py` | `Trading.TimeSales` (enriquecimiento) | Agrega TEA/TEM/Duration/Convexity/Paridad. Loop cada 5s, docs sin `duration` ordenados DESC |
| `engines/forwards.py` | `Trading.ForwardsLive` + `Trading.ForwardsHistorico` | Matriz NxN de tasas forward por curva cada 30s |
| `engines/breakevens.py` | `Trading.BreakevensLive` + `Trading.BreakevensHistorico` | Breakeven inflación mensual implícita CER/Lecap cada 30s |
| `engines/caucion.py` | `Trading.CaucionSnapshot` + `Trading.Caucion` | Caución ARS + USD del plazo correspondiente al próximo día hábil (1D/3D/4D según calendario). Snapshot 5s, cierre histórico al apagado |
| `engines/futuros_dlr.py` | `Trading.FuturosDLRSnapshot` + `Trading.FuturosDLR` | Outrights DLR vigentes (underlying 'Dólar USA A3500', cficode FXXXSX, 1 slash). Tasa implícita TNA calculada vs MEP spot. Snapshot 5s |
| `engines/dolares.py` | `Valuaciones.DolarSnapshot` | Live MEP/CCL/canje via WS (AL30/AL30D/AL30C). 1 doc `_id='current'` replaced cada 5s |
| `engines/dolar_mep.py` | `Valuaciones.Dolar` | Cron cada 15 min que escribe histórico (REST puntual). Complementa `dolares.py` |

### Trading.TimeSales

Campos base: `ticker`, `timestamp`, `price`, `size`, `side` (BUY/SELL/MID), `money`.

Campos enriquecidos por `engines/curvas.py` (solo tickers en `Trading.Curvas`):

| Campo | Instrumentos | Descripción |
|---|---|---|
| `duration` | todos | Macaulay duration en años |
| `convexity` | tasa_fija + cer | Segunda derivada del precio respecto al yield (años²) |
| `TEA` | tasa_fija + cer | Tasa efectiva anual |
| `TEM` | tasa_fija | Tasa efectiva mensual |
| `paridad` | cer | precio / (VN × CER_trade/CER_emision) × 100 |

### Trading.MarketSnapshot

Un doc por ticker, reemplazado cada 1s. Campos: `ticker`, `updated_at`, `book` (bids/offers top 5), `metrics` (micro_price, spread, imbalance, VWAP, VPIN, total_nominals, total_money, buy_money, sell_money, last/open/high/low/closing_price), `hourly_stats` (por hora 10-17), `top_trades` (top 15 por size), `recent_trades` (últimos 30).

### Trading.Curvas — estructura de flujos

Flujos CER usan campos porcentuales (NO valores absolutos):
- `amortizacion_pct`: % del VN que se amortiza
- `cupon_sobre_residual`: tasa × `residual_previo_pct` / 100 × VN
- `cupon_anual`: solo zero coupon (= 0)

Flujos tasa_fija usan valores absolutos: `amortizacion` + `interes`.

### Cálculo cuantitativo (`quant/`)

- `quant/black_scholes.py`: `bs_price()`, `bs_delta()`, `bs_gamma()`, `bs_vega()`, `bs_theta()`, `find_iv()` (Newton-Raphson), `calc_intrinseco()`. Lee `VR-GGal` de Mongo para HV.

### Jobs batch (`jobs/`)

- **`aunesa_client.py`** — cliente Aunesa API (auth + posicionValuada). Importado por el resto.
- **`carteras.py`** — sincroniza posiciones Aunesa → `Valuaciones.Carteras`. Clave upsert: `(id_cuenta, unidad)`. `filtrar_unidades()` excluye `{ARS, USDL}` y strings con `[1] Depósito U$`, `OTC`, `2024`, `2025`, `DLR`. Flag `--clean`.
- **`aum.py`** — snapshot AuM de TODAS las cuentas activas → `Valuaciones.AuM`. Clave: `(id_cuenta, unidad, fecha_snapshot)`. Fórmulas: P×Q/100 renta fija; (P+1)×Q futuros; P×Q resto. Retry 3 intentos / 60s. Sincroniza `Valuaciones.CarterasII` al final (primer día hábil mes anterior). Cron 23:00 UTC.
- **`aum_backfill.py`** — re-ejecutable, reconstruye AuM por fechas. `python -m jobs.aum_backfill <fecha>`.
- **`aum_resumen_fci.py`** — rollup → `Valuaciones.AuMResumenFCI`. Un doc por `fecha_snapshot` con `unidades[].{unidad, valuacion_total}` (~22 docs vs ~2.4k raw).
- **`cashflow.py`** — movimientos Aunesa → `CashFlow.Movimientos`. Índice único por `comprobante`. Depósitos positivos. `--today` para cron.
- **`flujo_contrapartes.py`** — operaciones del día → `CashFlow.Flujo`. Borra docs donde `concertacion == hoy`, fetch por contraparte, filtra 4 tipos excluidos, agrega `moneda`. Cron 22:00 UTC L-V.
- **`segmento_contrapartes.py`** — asigna `segmento` ("Fondos"/"ALYC"/"Bancos") en `CashFlow.Contrapartes`.
- **`options_rollup.py`** — rollup diario `Opciones.Data` → `Opciones.DataHistorica`. Upsert idempotente. `--backfill` / `--fecha YYYY-MM-DD`. Cron 20:15 UTC L-V.
- **`bcra.py`** — CER/TAMAR/DOLAR/BADLAR desde API BCRA. `--today` para cron; sin flag backfill desde 2023-01-01.
- **`cleanup_curvas.py`** — elimina instrumentos vencidos de `Trading.Curvas` (< 2 días hábiles). `--dry` para preview. Cron 12:30 UTC L-V.
- **`sync_api_copies.py`** — re-sincroniza colecciones derivadas `*API.*API` llamando a `scripts.api_migrate`. Flags: `--aum`, `--carteras`, `--flujo`, `--movimientos`, `--titulos`, `--all`. Encadenado en crontab después de cada job fuente.
- **`dias_habiles.py`** — genera calendario hábil argentino. Ejecutar una vez por año.

### Scripts de diagnóstico (`scripts/`)

- **`crear_indices.py`** — crea todos los índices MongoDB. Idempotente.
- **`perf_scan.py`** — análisis estático: `PERF001` find sin projection, `PERF002` query en for (N+1), `PERF003` count_documents({}), `PERF004` query repetida. Suprimir por línea con `# noqa: PERF00X`.
- **`perf_profile.py`** — profiling runtime de endpoints críticos (traces de latencia Mongo).
- **`check_cer.py`** — diagnóstico serie `Trading.CER` (huecos, últimos valores).
- **`check_cer_valuacion.py`** — CER usado en último trade enriquecido por bono.
- **`check_tasa_fija.py`** — diagnóstico join chain `Trading.Curvas` (tasa_fija) → `Assets` → `AuM`.
- **`check_curvas_pendientes.py`** — docs sin `duration` por ticker en TimeSales.
- **`check_forwards.py`** — diagnóstico completo de forwards por curva.
- **`check_aum_raw.py`** — consulta directa Aunesa filtrando por keyword.
- **`debug_forward.py`** — walk-through paso a paso del cálculo forward TX26 vs TZX26.
- **`test_match_contrapartes.py`** — verifica matcheo contrapartes Flujo ↔ Contrapartes.
- **`test_gemini.py`** — smoke test Gemini API (flash y pro). Flags `--modelo`, `--prompt`.
- **`test_chat.py`** — smoke test `/api/chat` contra localhost o prod. Requiere uvicorn corriendo.

## Deployment

**Servidor**: Droplet DigitalOcean, `root` en `/root/TradingAV/`, venv en `/root/TradingAV/venv/`.

**Servicios always-on**:
- `cloudflared.service` — Cloudflare Tunnel
- `api.service` — FastAPI (uvicorn), `127.0.0.1:8000`

**Servicios de mercado** (L-V, horario de mercado):
- `motor_rofex.service` → `python -m engines.valores`
- `motor_options.service` → `python -m engines.options`
- `motor_curvas.service` → `python -m engines.curvas`
- `motor_forwards.service` → `python -m engines.forwards`
- `motor_breakevens.service` → `python -m engines.breakevens`
- `motor_caucion.service` → `python -m engines.caucion`
- `motor_futuros_dlr.service` → `python -m engines.futuros_dlr`
- `motor_dolares.service` → `python -m engines.dolares`

Todas las `.service` usan `WorkingDirectory=/root/TradingAV` + `ExecStart=/root/TradingAV/venv/bin/python -m engines.<nombre>`.

### Crontab

Fuente de verdad: **`deploy/crontab.txt`**. Aplicar: `crontab /root/TradingAV/deploy/crontab.txt`.

| Horario UTC | Job | Frecuencia |
|---|---|---|
| 12:30 | `jobs.cleanup_curvas` | L-V |
| 13:00 / 20:05 | start/stop motores de mercado (8 motores: valores, options, curvas, forwards, breakevens, caucion, futuros_dlr, dolares) | L-V |
| 11:35 / 14:00 / 16:00 | `jobs.carteras` | L-V |
| `*/15 13-20` | `engines.dolar_mep` (histórico complementario al motor WS) | L-V |
| 20:00 | `jobs.volatilidad_ggal` + `jobs.bcra --today` | L-V |
| 23:00 | `jobs.aum` (+ CarterasII sync) | L-V |
| 23:30 | `jobs.aum_resumen_fci` | L-V |
| 20:15 | `jobs.options_rollup` | L-V |
| 22:00 | `jobs.flujo_contrapartes` + `jobs.market_anchors` | L-V |
| 02:00 | `jobs.cashflow --today` | Mar-Sáb |
| 04:00 / 11:20 | `deploy/atlas_cluster.sh {pause,resume}` | diario |
| `*/15 12-23` | `jobs.news_ingesta` | diario |
| `*/30 12-23` | `jobs.news_finnhub` | diario |
| `* 13-21` | `jobs.market_quotes` (cada 1 min horario US) | L-V |

**Atlas cluster pause**: se pausa entre 01:00–08:30 ART (04:00–11:30 UTC) todos los días. Durante la pausa la API devuelve error de conexión. Resume a 11:20 UTC con 10 min de buffer. Ahorro ≈ 31% sobre compute.

## REST API (`api/`)

FastAPI consumida exclusivamente por acaquant-web (a través de sus API routes proxy).

### Endpoints

| Método | Ruta | Colección | Query params |
|---|---|---|---|
| GET | `/api/health` | — | — |
| GET | `/api/cuentas/accionistas` | `CuentasAPI.AccionistasAPI` | — |
| GET | `/api/cuentas/contrapartes` | `CuentasAPI.ContrapartesAPI` | — |
| GET | `/api/operaciones/flujo` | `OperacionesAPI.MesaAPI` | `contraparte`, `moneda`, `segmento`, `desde`, `hasta` |
| GET | `/api/operaciones/flujos` | `OperacionesAPI.FlujosAPI` | `cuenta`, `unidad`, `desde`, `hasta` |
| GET | `/api/portfolio/carteras` | `PortfolioAPI.CarterasAPI` | `id_cuenta`, `unidad` |
| GET | `/api/portfolio/aum` | `PortfolioAPI.AumAPI` | `id_cuenta`, `unidad`, `cuenta`, `desde`, `hasta`, `ultimo` |
| GET | `/api/titulos/assets` | `TitulosAPI.AssetsAPI` | `unidad`, `ticker`, `cartera`, `emisor`, `clase_activo` |
| GET | `/api/titulos/flujos` | `TitulosAPI.ValuacionesAPI` | `ticker`, `curva`, `moneda_flujo` |
| GET | `/api/cotizaciones/badlar` | `Trading.BADLAR` | `desde`, `hasta` |
| GET | `/api/cotizaciones/cer` | `Trading.CER` | `desde`, `hasta` |
| GET | `/api/cotizaciones/dolar` | `Trading.DOLAR` | `desde`, `hasta` |
| GET | `/api/cotizaciones/mep` | `Valuaciones.DolarSnapshot` (live) + fallback `Valuaciones.Dolar` | — |
| GET | `/api/cotizaciones/forwards` | `Trading.ForwardsLive` | `curva` |
| GET | `/api/cotizaciones/renta-fija` | `Trading.MarketSnapshot` | `instrumento` |
| GET | `/api/cotizaciones/breakevens` | `Trading.BreakevensLive` | — |
| GET | `/api/cotizaciones/opciones` | `Opciones.OptionsSnapshot` | `instrumento`, `tipo` |
| GET | `/api/cotizaciones/caucion` | `Trading.CaucionSnapshot` | `moneda` |
| GET | `/api/cotizaciones/futuros-dlr` | `Trading.FuturosDLRSnapshot` | — |
| GET | `/api/cotizaciones/argy` | agregador MEP/CCL/canje/caución con returns | — |
| GET | `/api/cotizaciones/historico/forwards` | `Trading.ForwardsHistorico` | `curva`, `desde`, `hasta` |
| GET | `/api/cotizaciones/historico/breakevens` | `Trading.BreakevensHistorico` | `desde`, `hasta` |
| GET | `/api/cotizaciones/historico/mep` | `Valuaciones.Dolar` (serie) | `desde`, `hasta` |
| GET | `/api/cotizaciones/historico/trades` | `Trading.TimeSales` (últimos 15 días) | `instrumento` |
| GET | `/api/cotizaciones/historico/curva` | `Trading.TimeSales` (serie diaria) | `instrumento`, `desde`, `hasta` |
| GET | `/api/cotizaciones/historico/caucion` | `Trading.Caucion` | `moneda`, `desde`, `hasta` |
| GET | `/api/cotizaciones/historico/futuros-dlr` | `Trading.FuturosDLR` | `ticker`, `desde`, `hasta` |
| GET | `/api/analitica/listar-curva` | `Trading.Curvas` + `TimeSales` + `MarketSnapshot` | `curva` (req), `ordenar_por`, horizonte, limit |
| GET | `/api/analitica/serie-macro` | ver `_MACROS` en `services/macro.py` | `variable` (req), `ventana_dias` |
| GET | `/api/analitica/clasificar-nivel` | idem serie-macro (wrapper compacto) | `variable` (req), `ventana_dias` |
| GET | `/api/analitica/snapshot-curva-historico` | `Trading.TimeSales` (agregado por día) | `curva` (req), `fecha` (req) |
| GET | `/api/analitica/pendiente-curva` | reusa listar_curva + snapshot histórico | `curva` (req), `metrica`, `fecha_comparacion` |
| GET | `/api/analitica/liquidez-secundario` | `Trading.TimeSales` (agregado por día) | `ticker` (req), `dias` |
| GET | `/api/portfolio/resumen` | `CarterasAPI` + `CarterasII` + `AssetsAPI` | `id_cuenta` |
| GET | `/api/portfolio/detalle` | `CarterasAPI` + `AssetsAPI` | `id_cuenta` |
| GET | `/api/portfolio/tasa-fija` | `AumAPI` + `AssetsAPI` + `ValuacionesAPI` | — |
| GET | `/api/portfolio/cer` | `AumAPI` + `AssetsAPI` + `Trading.Curvas` | — |
| GET | `/api/news` | `Manager.News` (RSS + Finnhub) | — |
| GET | `/api/news/article` | reader mode vía trafilatura | `url` |
| GET | `/api/news/stats` | — | — |
| GET | `/api/market/quotes` | watchlist equity + UST (incluye anchors 7d/MTD/YTD/1Y) | — |
| GET | `/api/market/calendar/economic` | Finnhub economic calendar | — |
| GET | `/api/market/candle` | velas Yahoo Finance (yfinance) | — |
| GET | `/api/market/profile` | Finnhub company profile | — |
| GET | `/api/manager/status` | múltiples colecciones (read) | — |
| POST | `/api/manager/jobs/run` | subprocess en background | `tipo`, `args` |
| GET | `/api/manager/jobs/{id}` | in-memory job store | — |
| GET | `/api/manager/changelog` | `Manager.ChangeLog` | `limit` |
| GET | `/api/manager/latencia` | benchmark todas las colecciones | — |
| GET | `/api/manager/checks/*` | validaciones de datos | ver abajo |
| POST | `/api/chat` | asistente conversacional (Gemini + tool-use) | body: `message`, `history?` |

### Patrón de migraciones (colecciones API)

Las colecciones originales son la **fuente de verdad**. Las colecciones API son copias derivadas optimizadas para consumo externo.

**Flujo para nuevo endpoint:**
1. Definir mapping de campos en `scripts/api_migrate.py` (patrón: `drop()` + `insert_many()`, idempotente).
2. Crear router/endpoint en `api/routers/`.
3. Agregar DB helper en `api/deps.py` si es DB nueva.
4. Documentar en `docs/API_MIGRATIONS.md` y `docs/API.md`.

**Re-sincronización:** `python -m scripts.api_migrate <comando>`.

### Colecciones API actuales

| Origen | Destino | Comando migrate |
|---|---|---|
| `CashFlow.Accionistas` | `CuentasAPI.AccionistasAPI` | `accionistas` + `mover` |
| `CashFlow.Contrapartes` | `CuentasAPI.ContrapartesAPI` | `contrapartes` + `mover` |
| `CashFlow.Flujo` | `OperacionesAPI.MesaAPI` | `flujo` |
| `CashFlow.Movimientos` | `OperacionesAPI.FlujosAPI` | `movimientos` |
| `Valuaciones.Carteras` | `PortfolioAPI.CarterasAPI` | `carteras` |
| `Valuaciones.AuM` | `PortfolioAPI.AumAPI` | `aum` |
| `Valuaciones.Assets` | `TitulosAPI.AssetsAPI` | `assets` |
| `Trading.Curvas` + `Trading.BondsMaster` | `TitulosAPI.ValuacionesAPI` | `flujos-titulos` |

## Asistente de mesa (IA generativa)

**Doc completo**: [`docs/ASISTENTE.md`](docs/ASISTENTE.md) — fuente de verdad del módulo (arquitectura, archivos, data flow, observabilidad, roadmap, troubleshooting).

### Resumen

Módulo `api/agent/` + endpoint `POST /api/chat` + vistas en acaquant-web:
- `/asistente` — chat.
- `/manager` tab **ASISTENTE** — dashboard live de observabilidad.
- `/manager` tab **INTEL** — carga de reportes con extracción estructurada.

**Provider activo**: Claude con router automático Haiku/Sonnet (`LLM_PROVIDER=claude`). Gemini Flash queda como fallback legacy (`LLM_PROVIDER=gemini`).

**Arquitectura en 1 línea**: el runner usa formato canónico estilo Claude; cada provider convierte a su API. El modelo recibe un menú de tools, decide qué llamar (ninguna toca Mongo directo — todas invocan endpoints HTTP existentes), y el runner itera hasta que el modelo devuelve texto final o se llega a `MAX_STEPS=6`.

### Archivos clave

- `api/agent/types.py` — tipos neutros (`LLMResponse`, `ToolCallRequest`, helpers de mensajes).
- `api/agent/provider.py` — `ClaudeProvider` + `GeminiProvider` + factory `get_provider()`.
- `api/agent/router.py` — `decide_model(user_message)` → Haiku o Sonnet por heurísticas.
- `api/agent/runner.py` — bucle tool-use provider-agnostic.
- `api/agent/prompt.py` — `SYSTEM_PROMPT_BASE` compacto (~1.5K tokens) + `build_system_prompt()`.
- `api/agent/context.py` — "foto del día" (MEP, CER, top volumen, vtos, breakevens, últimas emisiones, bloque INTEL del último reporte).
- `api/agent/data_inventory.py` — introspección automática de Mongo.
- `api/agent/estrategia.py` / `estrategias.py` — loaders de los .md editables del framework y catálogo.
- `api/agent/intel_extraction.py` — extracción estructurada con JSON mode.
- `api/agent/tools.py` — `TOOLS[]` (17 tools), `dispatch()`, bloqueo Grupo 3.

### Política de datos

Tools bloqueadas por policy (`BLOCKED_PATH_PREFIXES`): `/api/portfolio/*`, `/api/operaciones/*`, `/api/cuentas/*`, `/api/manager/*`. **Doble cinturón**: se filtran al declararlas al modelo y se revisan de nuevo en `dispatch()`.

Con Claude y ZDR activado el bloqueo se puede relajar (roadmap). Con Gemini free tier queda firme porque Gemini entrena con la data.

### Variables de entorno

```
ANTHROPIC_API_KEY=sk-ant-api03-...  # obligatoria si LLM_PROVIDER=claude
GEMINI_API_KEY=AIzaSy...            # obligatoria si LLM_PROVIDER=gemini o para intel_extraction
LLM_PROVIDER=claude                  # claude (default) | gemini
```

### Observabilidad

- **Manager.AsistenteLogs**: cada turno (incluyendo errores). Dashboard live en `/manager` tab ASISTENTE.
- **Manager.IntelDocs**: reportes cargados, con extracción estructurada + texto crudo. Tab `/manager` INTEL.

### Archivos editables sin tocar código

- `docs/asistente/estrategia.md` — ADN analítico de la mesa (framework de 4 capas, house view, señales).
- `docs/asistente/estrategias.md` — catálogo técnico de ~45 estrategias por asset class.

Ambos se releen automáticamente cuando cambia el mtime. No hace falta restart.

## Lógica de Negocio (consolidada)

### 1. Valuación AuM

Fórmula por tipo (`jobs/aum.py` y `api/routers/carteras.py::_valuacion_api`):

- **Renta fija** (`Títulos Públicos`, `Letras`, `ONs`, `Fideicomisos`, `CPD`) → `cantidad × precio / 100`
- **FCI / OTROS** → `cantidad × precio`
- **Futuros** → `(precio + 1) × cantidad`

Constante: `TIPOS_DIVISOR_100 = {Títulos Públicos, Letras, ONs, Fideicomisos, CPD}`.

### 2. Breakevens

Fórmulas (`engines/breakevens.py`):
- `retorno = (1+TEM)^(días/30) - 1`
- `inflacion = (1+retorno) × (paridad/100) - 1`
- `breakeven = (1+inflacion)^(30/días) - 1`

Emparejamiento: cada Lecap con el CER de vencimiento más cercano (máx 60 días de diferencia).

### 3. Simulador Breakevens

P&L relativo **CER vs Lecap** bajo escenarios de inflación mensual flat:
- Settlement T+1; CER liq = settlement − 10 días hábiles.
- `ret_lecap = flujo_vencimiento / precio_lecap − 1`.
- `CER_proy = cer_liq × (1 + infl)^meses` → `flujo_pesos = monto_VN × CER_proy / cer_emision`.
- `ret_cer = Σ flujo_pesos / precio_cer − 1` → `P&L = ret_cer − ret_lecap` (bps).

### 4. Forwards

Fórmula: `((1 + TEA_B)^t_B / (1 + TEA_A)^t_A)^(1/(t_B - t_A)) - 1`.
Requiere TEA en TimeSales (escrito por `engines/curvas.py`, lag ~5s aceptable).
Matriz NxN por curva re-escrita cada 30s en `Trading.ForwardsLive`; snapshot diario en `Trading.ForwardsHistorico`.

### 5. Flujo vs AuM

`CashFlow.Contrapartes` (`segmento=="Fondos"`) → lista fondos → `Valuaciones.Assets` (`CARTERA=="CARTERA FCI"`) → `unidad` → `Valuaciones.AuM` (`$group` server-side).
En paralelo: `CashFlow.Flujo` filtrado por `contraparte ∈ fondos` y `moneda=="ARS"`.

### 6. AuM FCI — pipeline

```
Valuaciones.AuMResumenFCI (rollup, 1 doc/fecha)  ← job aum_resumen_fci.py
Valuaciones.AuM raw (drill-down por fecha)
Assets.CARTERA=='CARTERA FCI'
```

### 7. AuM Tasa Fija / CER — join chain

`Trading.Curvas` (`curva=='tasa_fija'`) → `ticker_corto` → `Valuaciones.Assets` (`TICKER==ticker_corto`) → `unidad` → `Valuaciones.AuM`. Idem con `curva=='cer'`.

Para agregar instrumento: insertar doc en `Trading.Curvas` + doc en `Valuaciones.Assets` con `TICKER == ticker_corto`.

### 8. Cash Flow — filtros

1. Rango fecha
2. `unidad ∈ monedas_sel`
3. Filtro accionistas: `"Todas"` / `"Sin accionistas"` / `"Solo accionistas"` (agrupa por `CashFlow.Accionistas`) / `"Solo cooperativas"` (regex `\bcoop`, excluye accionistas)

### 9. Portfolios Reportes — dataflow

- Mes actual: `Valuaciones.Carteras` (último snapshot Aunesa, valuación recalculada con regla 1)
- Mes anterior: `Valuaciones.CarterasII` (snapshot manual primer día hábil mes anterior, `valuacion` pre-calc)
- Benchmarks: `Valuaciones.Benchmarks` — `(periodo, benchmark)` → `mensual`, `acumulado`. `benchmark` ∈ `{"Badlar", "A3500", "Inflacion"}`, `periodo`: `"mmm-aa"`.
- Rendimientos: `Valuaciones.Rendimientos` — `(id_cuenta, periodo)` → `rendimiento_ars`, `rendimiento_usd`, `rendimiento_carteraars`. Carga manual.

## Colecciones de referencia

### Trading
- **`CER`** — Serie BCRA id=30. `fecha`, `valor`.
- **`TAMAR`** — BCRA id=44.
- **`DOLAR`** — A3500, BCRA id=5.
- **`BADLAR`** — BCRA id=7.
- **`Curvas`** — Definición estática renta fija. `ticker`, `ticker_corto`, `tipo`, `curva` (tasa_fija/cer), `fecha_vencimiento`, `fecha_emision`, `flujo_vencimiento`, `valor_nominal`, `cupon_anual`, `cer_emision`, `flujos[]`. Cargada manualmente.
- **`DiasHabiles`** — Calendario hábil argentino. `jobs/dias_habiles.py` una vez por año.

### Valuaciones
- **`Carteras`** — Último snapshot Aunesa. Clave `(id_cuenta, unidad)`.
- **`CarterasII`** — Snapshot manual primer día hábil mes anterior. `valuacion` pre-calculado.
- **`AuM`** — Snapshot diario `(id_cuenta, unidad, fecha_snapshot)`.
- **`AuMResumenFCI`** — Rollup 1 doc/`fecha_snapshot` con `unidades[].{unidad, valuacion_total}`.
- **`Assets`** — Metadata por `unidad`: `CARTERA`, `EMISOR`, `TICKER`, `CLASE_ACTIVO`, `CALIFICACION`, `VENCIMIENTO`. Unique index en `unidad`.
- **`Dolar`** — Snapshot MEP intradía.
- **`Benchmarks`** / **`Rendimientos`** — carga manual (ver sección Portfolios).

### CashFlow
- **`Flujo`** — Campos: `boleto` (clave única partial int), `concertacion`, `tipoOperacion`, `cuenta`, `denominacion`, `instrumento`, `condiciones`, `bruto`, `segmento` (de mercado Aunesa, distinto del `segmento` de Contrapartes), `contraparte`, `moneda`.
- **`Contrapartes`** — `contraparte` (join con Flujo), `cuenta`, `denominacion`, `segmento` ("Fondos"/"ALYC"/"Bancos").
- **`Accionistas`** — manual. `{cuenta, accionista}`. Consolida múltiples comitentes bajo un nombre.

## MongoDB Índices

Definidos en `scripts/crear_indices.py` (idempotente).

| Colección | Índice |
|---|---|
| `Trading.TimeSales` | `(ticker, timestamp)`, `(ticker, duration, timestamp)`, `(ticker, TEA, timestamp)`, `(ticker, TEM, timestamp)`, `(ticker, paridad, timestamp)` |
| `Trading.MarketSnapshot` | `ticker` |
| `Trading.ForwardsHistorico` | `(curva, fecha)` |
| `Trading.BreakevensHistorico` | `fecha` |
| `Trading.CER` | `fecha` |
| `Trading.Curvas` | `curva`, `ticker_corto` |
| `Valuaciones.AuM` | `fecha_snapshot`, `(unidad, fecha_snapshot)`, `(id_cuenta, fecha_snapshot)`, `(cuenta, fecha_snapshot)` |
| `Valuaciones.AuMResumen` | `(id_cuenta, unidad, fecha_snapshot)`, `(CARTERA, fecha_snapshot)`, `(EMISOR, fecha_snapshot)` |
| `Valuaciones.Carteras` | `(id_cuenta, unidad)` |
| `Valuaciones.Dolar` | `timestamp` |
| `Valuaciones.Assets` | `unidad` **(unique)**, `(EMISOR, CARTERA)` |
| `CashFlow.Flujo` | `(contraparte, moneda)`, `concertacion`, `boleto` **(unique, partial: `$type: int`)** |
| `CashFlow.Movimientos` | `fecha` |
| `Opciones.DataHistorica` | `fecha`, `(symbol, fecha)` |

## Notas técnicas

- **MongoClient**: nunca llamar `client.close()`. Singleton compartido; cerrarlo mata el pool.
- **Dos clientes MongoDB**: `get_mongo_client()` (read-write) para motores y jobs. `get_mongo_client_read()` (`SECONDARY_PREFERRED`) para la API. `serverSelectionTimeoutMS=30000` para tolerar elecciones en M10.
- **Ejecución siempre desde la raíz**: `python -m <módulo>` con `cwd=/root/TradingAV`. `python engines/valores.py` falla porque `core` no es discoverable.
- **En el servidor**: siempre `/root/TradingAV/venv/bin/python`.
- **Enriquecimiento CER**: el CER usado depende del settlement del trade (T-10 días hábiles). Si un bono no opera un día, su último trade puede usar el CER de ayer.
- **CashFlow DB**: nombre `CashFlow` (sin espacio). Depósitos positivos, extracciones negativas.
- **ruff errores comunes**: `F401` (import no usado tras mover código) y `F821` (nombre no definido — suele ser `datetime`/`timedelta` removido al limpiar imports).

## Pendientes

### Completados

- [x] **(2026-04-16)** Cron para sincronizar colecciones API automáticamente. Implementado via `jobs/sync_api_copies.py` encadenado en `deploy/crontab.txt` después de cada job: `--carteras` (3×/día), `--movimientos`, `--flujo`, `--aum --titulos` (flujos-titulos se re-sync diario post-cierre).
- [x] **(2026-04-19)** Migración completa a acaquant-web finalizada. Streamlit y `dashboard/` eliminados del repo.
- [x] **(2026-04-19)** Asistente de mesa con Gemini 2.5 Flash + tool-use. Vista `/asistente` restringida por `MANAGER_EMAILS`. Free tier: solo data pública (Grupos 1 y 2).
- [x] **(2026-04-19)** Switch a Claude (Haiku/Sonnet con router automático) + prompt caching. Gemini queda como fallback legacy vía `LLM_PROVIDER=gemini`. Ver `docs/ASISTENTE.md`.
- [x] **(2026-04-19)** Observabilidad live del asistente: tab `/manager` ASISTENTE con métricas, charts, logs con expand, filtros. Errores tipados + UI amigable con retry.
- [x] **(2026-04-19)** Ingesta de reportes de research: tab `/manager` INTEL. PDF o paste → extracción estructurada (12 variables macro) con Gemini JSON mode → preview editable → persist. El último IntelDoc confirmado se inyecta automáticamente al contexto del asistente.
- [x] **(2026-04-19)** `docs/AUDIT.md` eliminado (obsoleto, mencionaba Streamlit). `docs/ASISTENTE.md` es la nueva fuente de verdad del módulo IA.
- [x] **(2026-04-20)** Tier 1 de seguridad API: SSRF block en `/api/news/article`, validación criptográfica del JWT de Cloudflare Access (`api/auth.py`), `require_manager` como gate server-side de `/api/manager/*` y `/api/chat`, rate limit por identidad vía slowapi (`/api/chat` 30/min-500/día, `/api/manager/jobs/run` 5/h-20/día). Error model tipado `{code, message, retryable, retry_after_s}`.
- [x] **(2026-04-20)** Tier 2 auditoría API: extracción de `api/services/*` (pura, sin FastAPI). El asistente dispatchea directo sobre el service registry — elimina HTTP loopback (~100-300 ms menos por turn) y permite tests deterministas sin levantar uvicorn. Cache `@cached(ttl=N)` vive en el service para que router y dispatch compartan hit.
- [x] **(2026-04-20)** `api/auth.py` acepta service token JWT de acaquant-web SSR: whitelist `CF_TRUSTED_SERVICE_TOKENS` (CSV de `common_name`s legítimos). Service tokens desconocidos → 401 con warning que incluye el `common_name` full para triaje.
- [x] **(2026-04-20)** `/api/analitica/*` — Tier 1 tools del asistente expuestas vía HTTP: `listar-curva`, `serie-macro`, `clasificar-nivel`.
- [x] **(2026-04-21)** `docs/API.md` reescrito: auth multi-capa, rate limits por endpoint, error model tipado, catálogo completo de rutas.
- [x] **(2026-04-21)** Refactors: A1 bug `dolar_mep.close()`, A2 move `stats.py` de agent/ a quant/, B3 reemplazar caches manuales en `carteras.py` por `@cached`, MongoManager movido de `core/mongo.py` a `engines/options.py`.
- [x] **(2026-04-21)** Engine loader compartido `engines/_curvas_loader.py` — 4 engines (valores, curvas, forwards, breakevens) que antes duplicaban lectura de `Trading.Curvas` ahora usan un único módulo.
- [x] **(2026-04-21)** Motor `engines/dolares.py` (WS live MEP/CCL/canje via AL30/AL30D/AL30C) → `Valuaciones.DolarSnapshot` (_id='current' replaced cada 5s). Engine existente `engines/dolar_mep.py` queda como cron de histórico (cada 15 min L-V).
- [x] **(2026-04-21)** Motor `engines/caucion.py` (TNA caución ARS + USD, plazo dinámico según próximo día hábil). Snapshot live 5s + cierre histórico al apagado.
- [x] **(2026-04-21)** Motor `engines/futuros_dlr.py` (outrights DLR/MMMYY con tasa implícita TNA vs MEP spot). Discovery dinámico cada 5 min. Snapshot 5s.
- [x] **(2026-04-21)** Convexity calculada por `engines/curvas.py` y persistida en `Trading.TimeSales.convexity`. Expuesta en `listar_curva()` y chequeada por invariante.
- [x] **(2026-04-21)** CCL + canje agregados a `engines/dolar_mep.py` (persistidos también en histórico). Variables `ccl`, `canje`, `caucion_ars`, `caucion_usd` desbloqueadas en `api/services/macro.py`.
- [x] **(2026-04-21)** Endpoint `/api/cotizaciones/argy` (agregador MEP/CCL/canje/caución ARS/USD con returns %Día/%7d/%MTD/%YTD calculados vs anchors históricos) + tool del agente `argy_overview`.
- [x] **(2026-04-21)** Frontend: top ticker con CCL/canje/caución. Watchlist con grupos ARGY (c/ returns) y FUTUROS ROFEX (curva DLR con TNA implícita). Sustituido grupo viejo "Commodities" (ETFs NY) por "Futuros" (CME/CBOT/COMEX/NYMEX/ICE + BTC/ETH). TradingView default MERVAL.
- [x] **(2026-04-21)** Tools Tier 2 del asistente: `snapshot_curva_historico(curva, fecha)`, `calcular_pendiente_curva(curva, metrica, fecha?)`, `liquidez_secundario(ticker, dias)`.
- [x] **(2026-04-21)** Scaffolding cliente BYMA (`core/byma.py` + `scripts/test_byma.py` + 22 tests unitarios). OAuth2 client_credentials con cache de token, rate limit, wrappers de los 4 métodos (underwriters, issuers, historical-placements, document-content). Pendiente desbloqueo en el portal BYMA (credenciales / aprobación de app).
- [x] **(2026-04-21)** `AutoRefresh` global eliminado del layout de acaquant-web. Los componentes que necesitan refresh live ya tienen polling propio con intervalos correctos. Fix del incidente de 502s de Cloudflare Bot Fight Mode.
- [x] **(2026-04-21)** `docs/FRONTEND_AUDIT.md` con reporte priorizado del frontend.
- [x] **(2026-04-21)** Fix serialización datetime en `tool_result_message` (rompía turnos del asistente cuando las tools devolvían fechas crudas de Mongo).

### Abiertos

- [ ] **(2026-04-16)** Borrar DB huérfana `CarterasAPI` de Atlas (renombrada a `PortfolioAPI`).
- [ ] **(2026-04-19)** Destrabar tools del Grupo 3 (cartera/AuM/operaciones) ahora que el default es Claude. Pendiente decidir policy con compliance + activar ZDR con Anthropic.
- [ ] **(2026-04-21)** BYMA Primarias Placements — scaffolding listo; esperando que la app en el portal BYMA tenga credenciales + scope `bymaPrimariasPlacements.read` habilitados. Una vez resuelto: schema `Licitaciones.Primarias`, job `jobs/byma_primarias.py`, endpoints `/api/licitaciones/*`, tools del agente.
- [ ] **(2026-04-21)** Hard Dollar enrichment: extender `engines/curvas.py` a Globales/Bonares con YTM Newton-Raphson + duration + convexity sobre flujos USD. Habilita `listar_curva("soberanos")` con tasas reales.
- [ ] **(2026-04-21)** Seed de bonos Dólar Linked (TZV26, TZV28, D15F7, etc) en `Trading.Curvas` con `curva: "dolar_linked"`.
- [ ] **(2026-04-21)** Macro externa faltante: Riesgo País EMBI+, IPC/IPIM INDEC, REM BCRA. Todos bloqueados por scraping/ingesta no implementada.
- [ ] **(2026-04-19)** Roadmap asistente (`docs/ASISTENTE.md` §9): feedback 👍/👎, suite de evals con `golden_set.yaml`, RAG sobre IntelDocs con Atlas Vector Search, email forwarding para ingesta automática, exportar conversación a PDF, modo análisis profundo con Opus, streaming UX.
