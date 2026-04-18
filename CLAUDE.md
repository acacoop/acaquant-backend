# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TradingAV es la plataforma cuantitativa de mercados argentinos (MERVAL/ROFEX). Streama datos en tiempo real, corre motores analíticos paralelos (microstructure, opciones, curvas, forwards, breakevens), persiste en MongoDB Atlas (cluster M10), y expone datos vía REST API (FastAPI) consumida por **acaquant-web** (Next.js), el frontend activo en `trading.acaquant.com`.

**Streamlit fue reemplazado.** El directorio `dashboard/` queda como código legacy — no agregar features ni queries nuevas ahí. Todo desarrollo de frontend nuevo va en el repo `acaquant-web` (Next.js, `/Users/nicomollo/PycharmProjects/acaquant-web`).

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
MANAGER_EMAILS     ← emails separados por coma con acceso al Manager (legacy Streamlit)
API_KEY            ← clave para autenticar requests a la API (vacío = sin auth, modo dev)
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
│   └── snapshot_writer.py    # writer background genérico
│
├── engines/                  # motores always-on (WS → Mongo)
│   ├── valores.py            # Trading.TimeSales + MarketSnapshot
│   ├── curvas.py             # enriquecimiento TEA/Duration
│   ├── options.py            # Opciones.OptionsSnapshot
│   ├── forwards.py           # Trading.ForwardsLive + Historico
│   ├── breakevens.py         # Trading.BreakevensLive + Historico
│   └── dolar_mep.py          # snapshot MEP intradía
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
│   └── dias_habiles.py       # calendario hábil argentino
│
├── quant/                    # cálculo puro (sin I/O de red)
│   └── black_scholes.py      # bs_price / bs_delta / bs_gamma / bs_vega / bs_theta / find_iv
│
├── api/                      # REST API (FastAPI) — consumida por acaquant-web
│   ├── main.py               # entrypoint FastAPI
│   ├── deps.py               # get_db_* helpers
│   └── routers/
│       ├── carteras.py       # /api/portfolio/*
│       ├── cotizaciones.py   # /api/cotizaciones/*
│       ├── cuentas.py        # /api/cuentas/*
│       ├── operaciones.py    # /api/operaciones/*
│       └── titulos.py        # /api/titulos/*
│
├── dashboard/                # LEGACY — Streamlit. No agregar features nuevas.
│
├── scripts/                  # one-shot / diagnóstico manual
│   ├── crear_indices.py      # idempotente
│   ├── api_migrate.py        # migraciones colecciones legacy → API
│   ├── test_api.py           # smoke test endpoints API
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
└── docs/                     # API.md, API_MIGRATIONS.md, AUDIT.md
```

**Regla de capas**: `core/` no importa a nadie. `engines/` y `jobs/` importan `core/` + `quant/`. `api/` usa `core.mongo.get_mongo_client_read()` (read-only). `scripts/` puede importar lo que necesite.

## Frontend: acaquant-web

Repo separado: `/Users/nicomollo/PycharmProjects/acaquant-web`. **Este es el frontend activo.**

**Stack**: Next.js 15 (App Router) + TypeScript + Tailwind CSS 4 + React 19. Lightweight Charts para gráficos trading-style, Recharts para el resto.

### Estructura acaquant-web

```
src/
├── app/
│   ├── layout.tsx            # Root layout: Header, TopTicker, AutoRefresh
│   ├── page.tsx              # DIARIO: renta fija, forwards, curvas, breakevens
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
  - `get_mongo_client_read()` → `MONGO_URI_READ` con `read_preference=SECONDARY_PREFERRED`. API y dashboard legacy. Fallback a `MONGO_URI` si no está definido.
  - **Nunca llamar `client.close()`** — son singletons de larga vida.

### Trading Engines

Cada motor tiene `update_price(ticker, data)` llamado por el WebSocket en cada tick.

| Motor | Colección MongoDB | Descripción |
|---|---|---|
| `engines/valores.py` | `Trading.TimeSales` + `Trading.MarketSnapshot` | Microestructura bonos/Lecaps/CER: inserta trades en TimeSales, snapshot cada 1s en MarketSnapshot |
| `engines/options.py` | `Opciones.OptionsSnapshot` | Opciones GGAL: Black-Scholes Greeks, IV via Newton-Raphson |
| `engines/curvas.py` | `Trading.TimeSales` (enriquecimiento) | Agrega TEA/TEM/Duration/Paridad. Loop cada 5s, docs sin `duration` ordenados DESC |
| `engines/forwards.py` | `Trading.ForwardsLive` + `Trading.ForwardsHistorico` | Matriz NxN de tasas forward por curva cada 30s |
| `engines/breakevens.py` | `Trading.BreakevensLive` + `Trading.BreakevensHistorico` | Breakeven inflación mensual implícita CER/Lecap cada 30s |

### Trading.TimeSales

Campos base: `ticker`, `timestamp`, `price`, `size`, `side` (BUY/SELL/MID), `money`.

Campos enriquecidos por `engines/curvas.py` (solo tickers en `Trading.Curvas`):

| Campo | Instrumentos | Descripción |
|---|---|---|
| `duration` | todos | Macaulay duration en años |
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
- **`dias_habiles.py`** — genera calendario hábil argentino. Ejecutar una vez por año.

### Scripts de diagnóstico (`scripts/`)

- **`crear_indices.py`** — crea todos los índices MongoDB. Idempotente.
- **`perf_scan.py`** — análisis estático: `PERF001` find sin projection, `PERF002` query en for (N+1), `PERF003` count_documents({}), `PERF004` query repetida. Suprimir por línea con `# noqa: PERF00X`.
- **`check_cer_valuacion.py`** — CER usado en último trade enriquecido por bono.
- **`check_curvas_pendientes.py`** — docs sin `duration` por ticker en TimeSales.
- **`check_forwards.py`** — diagnóstico completo de forwards por curva.
- **`check_aum_raw.py`** — consulta directa Aunesa filtrando por keyword.
- **`debug_forward.py`** — walk-through paso a paso del cálculo forward TX26 vs TZX26.

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

Todas las `.service` usan `WorkingDirectory=/root/TradingAV` + `ExecStart=/root/TradingAV/venv/bin/python -m engines.<nombre>`.

### Crontab

Fuente de verdad: **`deploy/crontab.txt`**. Aplicar: `crontab /root/TradingAV/deploy/crontab.txt`.

| Horario UTC | Job | Frecuencia |
|---|---|---|
| 12:30 | `jobs.cleanup_curvas` | L-V |
| 13:00 / 20:05 | start/stop motores de mercado | L-V |
| 11:35 / 14:00 / 16:00 | `jobs.carteras` | L-V |
| 14:00 / 19:57 | `engines.dolar_mep` | L-V |
| 20:00 | `jobs.volatilidad_ggal` + `jobs.bcra --today` | L-V |
| 23:00 | `jobs.aum` (+ CarterasII sync) | L-V |
| 23:30 | `jobs.aum_resumen_fci` | L-V |
| 20:15 | `jobs.options_rollup` | L-V |
| 22:00 | `jobs.flujo_contrapartes` | L-V |
| 02:00 | `jobs.cashflow --today` | Mar-Sáb |
| 04:00 / 11:20 | `deploy/atlas_cluster.sh {pause,resume}` | diario |

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
| GET | `/api/cotizaciones/mep` | `Valuaciones.Dolar` (último) | — |
| GET | `/api/cotizaciones/forwards` | `Trading.ForwardsLive` | `curva` |
| GET | `/api/cotizaciones/renta-fija` | `Trading.MarketSnapshot` | `instrumento` |
| GET | `/api/cotizaciones/breakevens` | `Trading.BreakevensLive` | — |
| GET | `/api/cotizaciones/opciones` | `Opciones.OptionsSnapshot` | `instrumento`, `tipo` |
| GET | `/api/cotizaciones/historico/forwards` | `Trading.ForwardsHistorico` | `curva`, `desde`, `hasta` |
| GET | `/api/cotizaciones/historico/breakevens` | `Trading.BreakevensHistorico` | `desde`, `hasta` |
| GET | `/api/cotizaciones/historico/mep` | `Valuaciones.Dolar` (serie) | `desde`, `hasta` |
| GET | `/api/cotizaciones/historico/trades` | `Trading.TimeSales` (últimos 15 días) | `instrumento` |
| GET | `/api/cotizaciones/historico/curva` | `Trading.TimeSales` (serie diaria) | `instrumento`, `desde`, `hasta` |

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

## Lógica de Negocio (consolidada)

### 1. Valuación AuM

Fórmula por tipo (`jobs/aum.py`, también en `dashboard/services/` legacy):

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

- [x] **(2026-04-16)** Cron para sincronizar colecciones API automáticamente. Implementado via `jobs/sync_api_copies.py` encadenado en `deploy/crontab.txt` después de cada job: `--carteras` (3×/día), `--movimientos`, `--flujo`, `--aum --titulos` (flujos-titulos se re-sync diario post-cierre).
- [ ] **(2026-04-16)** Borrar DB huérfana `CarterasAPI` de Atlas (renombrada a `PortfolioAPI`).
- [ ] **(2026-04-17)** Migrar vistas restantes de Streamlit a acaquant-web (operaciones, portfolios, aum).
