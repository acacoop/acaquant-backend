# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TradingAV is a quantitative trading platform for Argentine financial markets (MERVAL/ROFEX). It streams real-time market data, runs parallel analytical engines (microstructure, options, curvas, forwards, breakevens), persists state to MongoDB Atlas (cluster M10), y expone un dashboard Streamlit accesible vía `www.acaquant.com`.

## Acceso al Dashboard

Dos deploys activos en paralelo. La migración definitiva a acaquant está en curso.

| Deploy | URL | Auth | Estado |
|---|---|---|---|
| Streamlit Cloud | URL privada de streamlit.io | Sin login (URL secreta) | Activo — uso actual de usuarios |
| Droplet + Cloudflare | www.acaquant.com | Cloudflare Access (email OTP) | Activo — nuevo, en transición |

**acaquant.com**: corre en el Droplet de DigitalOcean. `cloudflared.service` (always-on, systemd) establece el tunnel hacia Cloudflare. Cloudflare Access exige autenticación por email OTP antes de llegar al servidor. `streamlit.service` también es always-on.

**Vista Manager restringida**: solo emails en `MANAGER_EMAILS` (`.env`) pueden ver y acceder al Manager. El email autenticado lo lee Streamlit del header HTTP `Cf-Access-Authenticated-User-Email` que inyecta Cloudflare. En local (sin Cloudflare) el Manager es accesible para todos.

## Variables de entorno requeridas (`.env`)

```
ROFEX_USER / ROFEX_PASSWORD / ROFEX_ACCOUNT / ROFEX_API_URL / ROFEX_WS_URL
MONGO_URI          ← usuario read-write (motores + Manager)
MONGO_URI_READ     ← usuario read-only (dashboard Streamlit)
AUNESA_CLIENT_ID / AUNESA_USERNAME / AUNESA_PASSWORD
MANAGER_EMAILS     ← emails separados por coma con acceso al Manager
```

## Estructura de carpetas

```
TradingAV/
├── streamlit_app.py          # entrypoint Streamlit (nav + router)
├── config.py                 # credenciales .env
│
├── core/                     # infra compartida (importada por todos)
│   ├── mongo.py              # singletons get_mongo_client() / get_mongo_client_read()
│   ├── mongo_monitor.py      # command listener para métricas Atlas
│   ├── profiler.py           # Stopwatch (traces de latencia del Manager)
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
│   ├── aum_backfill.py       # reconstrucción histórica (invocado por Manager)
│   ├── aum_resumen_fci.py    # rollup 1 doc/fecha → Valuaciones.AuMResumenFCI
│   ├── cashflow.py           # movimientos → CashFlow.Movimientos
│   ├── flujo_contrapartes.py # operaciones del día → CashFlow.Flujo
│   ├── segmento_contrapartes.py  # setea Fondos/ALYC/Bancos
│   ├── volatilidad_ggal.py   # VR histórica GGAL al cierre
│   ├── options_rollup.py     # rollup Opciones.Data → DataHistorica
│   ├── bcra.py               # CER/TAMAR/DOLAR/BADLAR
│   └── dias_habiles.py       # calendario hábil argentino
│
├── quant/                    # cálculo puro (sin I/O de red; lee Mongo para HV)
│   └── black_scholes.py      # bs_price / bs_delta / bs_gamma / bs_vega / bs_theta / find_iv
│
├── dashboard/                # todo Streamlit — arquitectura 3 capas
│   ├── shared/               # helpers compartidos entre vistas
│   │   ├── db.py             # get_db() / get_db_valuaciones() / get_db_cashflow() / get_db_opciones() (cache_resource)
│   │   ├── auth.py           # identificación via header Cf-Access
│   │   ├── format.py         # formatters ARS / USD / %
│   │   └── styles.py         # CSS y paleta de colores
│   ├── repos/                # capa 1: I/O a Mongo con @st.cache_data (read-only)
│   │   ├── portfolios.py     # Reportes (Carteras, CarterasII, Assets, Dolar, MEP)
│   │   ├── operaciones.py    # Cash Flow, Flujo vs AuM, Accionistas
│   │   ├── aum.py            # AuM último, FCI agg/snapshot, Assets map, Curvas
│   │   ├── opciones.py       # OptionsSnapshot, metadata, histórico EV
│   │   ├── mercado.py        # Breakevens, Forwards, Curvas, Precios, Volúmenes
│   │   └── manager.py        # fetch_latest + traces de latencia del Manager
│   ├── services/             # capa 2: lógica pura sobre DataFrames (sin I/O)
│   │   ├── portfolios.py     # build_carteras_enriquecidas + valuación AuM
│   │   └── operaciones.py    # filtrar_movimientos + es_cooperativa
│   └── views/                # capa 3: solo UI Streamlit (widgets + Altair)
│       ├── mercado.py        # Mercado · Libro · Curvas · Breakevens · Forwards · Retorno · Volúmenes
│       ├── opciones.py       # Cadena GGAL + Estrategias
│       ├── portfolios.py     # Reportes (informe ejecutivo mensual)
│       ├── operaciones.py    # Cash Flow · Contrapartes · Análisis · Flujo vs AuM
│       ├── aum.py            # FCI · Análisis SG · Tasa Fija · CER
│       └── manager.py        # Diagnóstico · Backfills · Validaciones · Logs · Setup · Latencia
│
├── scripts/                  # one-shot / diagnóstico manual
│   ├── crear_indices.py      # idempotente
│   ├── api_migrate.py        # migraciones colecciones legacy → API
│   ├── test_api.py           # smoke test endpoints API
│   ├── check_cer.py / check_cer_valuacion.py / check_curvas_pendientes.py
│   ├── check_forwards.py / check_tasa_fija.py / debug_forward.py
│   ├── check_aum_raw.py      # dump Aunesa por keyword
│   └── test_match_contrapartes.py
│
├── api/                      # REST API (FastAPI)
│   ├── main.py               # entrypoint FastAPI
│   ├── deps.py               # get_db_cuentas() / get_db_operaciones()
│   └── routers/
│       ├── cuentas.py        # /api/cuentas/*
│       └── operaciones.py    # /api/operaciones/*
│
├── deploy/
│   ├── systemd/              # 6 .service (motor_* + streamlit)
│   └── crontab.txt           # fuente de verdad del cron
│
├── assets/logo-header.png
├── docs/                     # API.md, API_MIGRATIONS.md, AUDIT.md, diccionario_rofex.xlsx
└── logs/                     # git-ignored
```

**Regla de capas**: `core/` no importa a nadie. `engines/` y `jobs/` importan `core/` + `quant/`. `dashboard/` lee Mongo vía `core.mongo.get_mongo_client_read()`; solo el Manager escribe. `api/` lee Mongo vía `core.mongo.get_mongo_client_read()` (read-only). `scripts/` puede importar lo que necesite.

## Arquitectura del Dashboard (repos / services / views)

Desde el refactor de abril-2026, cada vista Streamlit está dividida en **3 capas** con responsabilidades estrictas y sin behavior change respecto a la versión monolítica previa:

```
┌───────────────────────────────────┐
│  dashboard/views/<vista>.py       │  Widgets Streamlit + Altair. UI only.
│  (Capa 3)                         │  NO Mongo, NO lógica de negocio no trivial.
└────────────────┬──────────────────┘
                 │
┌────────────────▼──────────────────┐
│  dashboard/services/<vista>.py    │  Funciones puras sobre DataFrames.
│  (Capa 2)                         │  Reglas de dominio. NO Mongo, NO Streamlit.
└────────────────┬──────────────────┘
                 │
┌────────────────▼──────────────────┐
│  dashboard/repos/<vista>.py       │  Loaders cacheados (@st.cache_data).
│  (Capa 1)                         │  Única puerta a Mongo (read-only).
└────────────────┬──────────────────┘
                 │
┌────────────────▼──────────────────┐
│  dashboard/shared/db.py           │  get_db_* (@st.cache_resource).
│                                   │  Wrapea get_mongo_client_read().
└───────────────────────────────────┘
```

**Reglas estrictas**:

- **`repos/`**: cada función devuelve un `pd.DataFrame`, `dict` o primitivo. Decoradas con `@st.cache_data(ttl=..., show_spinner=False)`. TTL corto para datos live (5–60 s), largo para estáticos (300–900 s). Puede hacer agregaciones server-side (`$group`, `$match`, `$project`) pero NO aplica reglas de negocio. Toda proyección (`{campo: 1}`) se define acá para minimizar el transporte.
- **`services/`**: funciones puras sobre DataFrames ya cargados. Importan desde `repos/` o reciben el DF como argumento. No pueden importar `streamlit` ni `core.mongo`. Son testeables sin I/O.
- **`views/`**: solo orquestan (tabs, selectboxes, `st.columns`) y pintan. Delegan cualquier query a `repos/` y cualquier cálculo a `services/`.

### Loaders por vista (capa repos)

| Vista | Archivo | Funciones principales | TTL |
|---|---|---|---|
| Portfolios | `repos/portfolios.py` | `get_dolar_oficial`, `get_valor_mep`, `get_assets`, `get_carteras`, `get_carteras_ii` | 60–600 s |
| Operaciones | `repos/operaciones.py` | `get_movimientos` (regex año), `get_accionistas_map`, `get_flujo_contrapartes`, `get_fondos_flujo_aum` ($group server-side) | 600–900 s |
| AuM | `repos/aum.py` | `get_aum_ultimo`, `get_fci_unidades`, `get_aum_fci_agg` (rollup AuMResumenFCI), `get_aum_fci_snapshot`, `get_assets_map`, `get_curvas_tasa_fija`, `get_curvas_cer` + helper `parallel(*fns)` con ThreadPoolExecutor | 300–600 s |
| Opciones | `repos/opciones.py` | `get_options_snapshot`, `get_metadata`, `fetch_estrategia_historico`, `fetch_vol_historico` | 5–300 s |
| Mercado | `repos/mercado.py` | `get_breakevens_historico`, `get_datos_simulador`, `get_forwards_historico`, `get_tickers_curvas`, `get_volumen_diario_tickers`, `get_precios_intraday` (downsample 1-min server-side), `get_precios_diarios_curva` | 60–300 s |
| Manager | `repos/manager.py` | `fetch_latest(db, coll, field, filtro)` + `trace_aum_fci` / `trace_operaciones_cashflow` / `trace_mercado_libro` / `trace_opciones_mercado` (Stopwatch por etapa) + `TRACES` dict | sin cache |

### Services por vista (capa services)

- **`services/portfolios.py`** — `build_carteras_enriquecidas()` (hace merge con `Assets` y calcula `valuación`), `build_carteras_ii_enriquecidas()` (`valuacion` viene pre-calc del snapshot). Usa `_merge_with_assets()` para rellenar `TICKER/EMISOR/CLASE_ACTIVO/CARTERA/CALIFICACION/VENCIMIENTO` y `_calcular_valuacion(row)` para aplicar la regla P×Q (FCI/OTROS) vs P×Q/100 (renta fija).
- **`services/operaciones.py`** — `es_cooperativa(cuenta_str)` usa regex `\bcoop` case-insensitive. `filtrar_movimientos(df, rango, monedas_sel, filtro_acc, seleccion, acc_map)` aplica los filtros del Cash Flow (Todas / Sin accionistas / Solo accionistas / Solo cooperativas) con dropdown secundario.

**Manager no tiene `services/`** — es una vista admin con escrituras y flujos one-shot; la lógica vive inline en la view y los helpers de I/O están en `repos/manager.py`.

## Running the Project

Todo se ejecuta desde la raíz del proyecto con `python -m <módulo>`:

```bash
pip install -r requirements.txt        # deps incluyen pyRofex, rich

# Web dashboard
streamlit run streamlit_app.py

# Motores (systemd los corre como `python -m engines.<nombre>`)
python -m engines.valores              # TimeSales + MarketSnapshot
python -m engines.options              # Opciones GGAL headless
python -m engines.curvas               # enriquecimiento TEA/Duration
python -m engines.forwards             # tasas forward cada 30s
python -m engines.breakevens           # breakevens cada 30s
python -m engines.dolar_mep            # snapshot MEP (cron intradía)

# Jobs batch
python -m jobs.aum                     # snapshot AuM (cron 23:00 UTC) + sync CarterasII
python -m jobs.aum_resumen_fci         # rollup AuMResumenFCI (post-aum)
python -m jobs.carteras                # sync carteras (cron 4×/día)
python -m jobs.cashflow --today        # cron 02:00 UTC
python -m jobs.bcra --today            # cron 20:00 UTC diario

# REST API
uvicorn api.main:app --reload --port 8000

# Migraciones API (colecciones legacy → API)
python -m scripts.api_migrate accionistas
python -m scripts.api_migrate contrapartes
python -m scripts.api_migrate mover
python -m scripts.api_migrate flujo
python -m scripts.api_migrate movimientos

# Test endpoints API
python -m scripts.test_api                   # localhost:8000
python -m scripts.test_api http://host:port  # custom URL

# Scripts
python -m scripts.crear_indices
python -m scripts.check_forwards
```

No hay test suite ni linting configurado en CI local. GitHub Actions corre `ruff check` en cada push.

## Architecture

**Event-driven, multi-engine architecture:**

```
ROFEX WebSocket (pyRofex)
        │
        ▼
core.websocket.WebSocketManager
  - Subscribes tickers in 50-ticker chunks
  - Dispatches market data to engine handlers
        │
   ┌────┴────┬──────────┐
   ▼         ▼          ▼
engines.valores  engines.options  engines.curvas
        │
        ▼
MongoDB Atlas M10 (4 DBs: Trading, Opciones, Valuaciones, CashFlow)
        │
        ▼
Streamlit Dashboard (www.acaquant.com)
```

### Key Components

- **`config.py`** — Config centralizado; carga `.env` (credenciales ROFEX, Aunesa); `MANAGER_EMAILS` para control de acceso al Manager.
- **`core/rofex_session.py`** — Auth única de pyRofex (`inicializar_sesion`).
- **`core/websocket.py`** — `WebSocketManager`: suscripciones WS; registra handlers `update_price(ticker, data)` por motor.
- **`core/mongo.py`** — Dos clientes singleton thread-safe (double-checked locking). Ambos usan `serverSelectionTimeoutMS=30000`, `maxPoolSize=20`, `compressors="zstd,snappy,zlib"`:
  - `get_mongo_client()` → `MONGO_URI` (read-write). Usado por motores, crons y Manager.
  - `get_mongo_client_read()` → `MONGO_URI_READ` con `read_preference=SECONDARY_PREFERRED` (lee de réplicas para liberar primary; M10 lag <1s, aceptable para dashboard). Fallback a `MONGO_URI` si `MONGO_URI_READ` no está definido.
  - **Nunca llamar `client.close()`** — son singletons de larga vida; cerrarlos rompe el pool compartido con Streamlit.

### Trading Engines

Cada motor tiene `update_price(ticker, data)` llamado por el WebSocket en cada tick.

| Motor | Colección MongoDB | Descripción |
|---|---|---|
| `engines/valores.py` | `Trading.TimeSales` + `Trading.MarketSnapshot` | Microestructura bonos/Lecaps/CER: inserta trades en TimeSales, snapshot cada 1s en MarketSnapshot |
| `engines/options.py` | `Opciones.OptionsSnapshot` | Opciones GGAL: Black-Scholes Greeks, IV via Newton-Raphson. Headless (motor_options.service) |
| `engines/curvas.py` | `Trading.TimeSales` (enriquecimiento) | Agrega TEA/TEM/Duration/Paridad. Loop cada 5s, docs sin `duration` ordenados DESC para no bloquear con docs viejos irresolubles |
| `engines/forwards.py` | `Trading.ForwardsLive` + `Trading.ForwardsHistorico` | Matriz NxN de tasas forward por curva cada 30s |
| `engines/breakevens.py` | `Trading.BreakevensLive` + `Trading.BreakevensHistorico` | Breakeven inflación mensual implícita CER/Lecap cada 30s |

### Trading.TimeSales

Trades en tiempo real. Campos base (`engines/valores.py`): `ticker`, `timestamp`, `price`, `size`, `side` (BUY/SELL/MID), `money`

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

- `quant/black_scholes.py`: Black-Scholes — `bs_price()`, `bs_delta()`, `bs_gamma()`, `bs_vega()`, `bs_theta()`, `find_iv()` (Newton-Raphson), `calc_intrinseco()`. Además lee `VR-GGal` de Mongo para HV.

### Jobs batch (`jobs/`)

- **`aunesa_client.py`** — cliente Aunesa API (auth + posicionValuada). Importado por el resto.
- **`carteras.py`** — sincroniza posiciones Aunesa → `Valuaciones.Carteras`. Clave upsert: `(id_cuenta, unidad)`. Filtra unidades inválidas antes de guardar (`filtrar_unidades()`): excluye exactas `{ARS, USDL}` y las que contienen `[1] Depósito U$`, `OTC`, `2024`, `2025`, `DLR`. Flag `--clean` para borrar docs ya existentes con esas unidades.
- **`aum.py`** — snapshot AuM de TODAS las cuentas activas → `Valuaciones.AuM`. Clave: `(id_cuenta, unidad, fecha_snapshot)`. Fórmulas: P×Q/100 para renta fija (Títulos Públicos, ONs, Letras, Fideicomisos, CPD); (P+1)×Q para futuros; P×Q para el resto. Retry automático ante timeout Aunesa (3 intentos, 60s). Al final sincroniza `Valuaciones.CarterasII` si corresponde (primer día hábil del mes anterior). Cron 23:00 UTC.
- **`aum_backfill.py`** — re-ejecutable, reconstruye AuM por fechas. Usado desde el Manager (subprocess: `python -m jobs.aum_backfill <fecha>`). También sincroniza CarterasII al final.
- **`aum_resumen_fci.py`** — rollup `Valuaciones.AuM` (CARTERA FCI) → `Valuaciones.AuMResumenFCI`. **Un doc por `fecha_snapshot`** con array `unidades[]` (cada entry tiene `unidad` y `valuacion_total`). Objetivo: minimizar transporte de bytes al dashboard (~22 docs vs ~2.4k). Consumido por `repos/aum.py::get_aum_fci_agg()`.
- **`cashflow.py`** — movimientos de cash desde Aunesa → `CashFlow.Movimientos`. Índice único por `comprobante`. Signo invertido (depósitos positivos). `--today` para cron.
- **`flujo_contrapartes.py`** — operaciones del día desde Aunesa → `CashFlow.Flujo`. Borra docs donde `concertacion == hoy`, fetch por cada contraparte con `cuenta` asignada, filtra 4 tipos excluidos, agrega `moneda` (ARS/USD), deduplica por `boleto`. Cron 22:00 UTC L-V (19:00 ART, en mercado aún abierto).
- **`segmento_contrapartes.py`** — asigna `segmento` ("Fondos"/"ALYC"/"Bancos") en `CashFlow.Contrapartes`. Reglas automáticas + modo interactivo para sin match. Importado por `dashboard/views/manager.py`.
- **`volatilidad_ggal.py`** — VR histórica GGAL al cierre. Cron 20:00 UTC.
- **`options_rollup.py`** — rollup diario `Opciones.Data` → `Opciones.DataHistorica` (una fila por `(fecha, symbol)` con high/low/last/ev + griegas del último tick). Upsert idempotente. Cron 20:15 UTC L-V. `--backfill` procesa todos los días con datos en `Opciones.Data`; `--fecha YYYY-MM-DD` uno puntual.
- **`bcra.py`** — alimenta CER/TAMAR/DOLAR/BADLAR desde API BCRA. `--today` para cron; sin flag hace backfill desde 2023-01-01. SSL verificado (verify=True).
- **`dias_habiles.py`** — genera calendario de días hábiles argentinos. Ejecutar una vez por año.

### Scripts de diagnóstico (`scripts/`)

- **`crear_indices.py`** — crea todos los índices MongoDB necesarios. Idempotente. Soporta `unique` y `partialFilterExpression`. Ejecutar al agregar colecciones nuevas o en un servidor nuevo. Invocado también desde la tab Setup del Manager.
- **`check_cer_valuacion.py`** — muestra el CER usado en el último trade enriquecido por bono.
- **`check_curvas_pendientes.py`** — cuántos docs sin `duration` hay por ticker en TimeSales.
- **`check_forwards.py`** — diagnóstico completo de forwards por curva.
- **`check_tasa_fija.py`** — diagnóstico de instrumentos tasa_fija en AuM.
- **`check_aum_raw.py`** — consulta directa Aunesa, filtra por keyword. Invocado desde la tab Validaciones del Manager.
- **`test_match_contrapartes.py`** — match de contrapartes con Aunesa. Importado por `dashboard/views/manager.py`.
- **`debug_forward.py`** — walk-through paso a paso del cálculo forward TX26 vs TZX26.

## Deployment

**Servidor**: Droplet de DigitalOcean, `root` en `/root/TradingAV/`, venv local en `/root/TradingAV/venv/`.

**Servicios always-on** (arrancan con el servidor):
- `cloudflared.service` — Cloudflare Tunnel, siempre activo
- `streamlit.service` — dashboard Streamlit, siempre activo

**Servicios de mercado** (lunes a viernes, horario de mercado). Definidos en `deploy/systemd/` (copiar a `/etc/systemd/system/` en el servidor):

- `motor_rofex.service` → `python -m engines.valores`
- `motor_options.service` → `python -m engines.options`
- `motor_curvas.service` → `python -m engines.curvas`
- `motor_forwards.service` → `python -m engines.forwards`
- `motor_breakevens.service` → `python -m engines.breakevens`

Todas las `.service` usan `WorkingDirectory=/root/TradingAV` + `ExecStart=/root/TradingAV/venv/bin/python -m engines.<nombre>`.

### Crontab

Fuente de verdad: **`deploy/crontab.txt`**. Para aplicar en el servidor:

```bash
crontab /root/TradingAV/deploy/crontab.txt
```

Todos los jobs se invocan como `cd /root/TradingAV && /root/TradingAV/venv/bin/python -m <módulo>`. Logs en `/root/TradingAV/logs/`.

Resumen de horarios (ver `deploy/crontab.txt` para el detalle):

| Horario UTC | Job | Frecuencia |
|---|---|---|
| 13:00 / 20:05 | start/stop motores de mercado | L-V |
| 10:00 / 11:30 / 14:00 / 16:00 | `jobs.carteras` | L-V |
| 14:00 / 19:57 | `engines.dolar_mep` | L-V |
| 20:00 | `jobs.volatilidad_ggal` | L-V |
| 20:00 | `jobs.bcra --today` | todos los días |
| 23:00 | `jobs.aum` (+ CarterasII sync) | L-V |
| 23:30 | `jobs.aum_resumen_fci` | L-V |
| 20:15 | `jobs.options_rollup` | L-V |
| 22:00 | `jobs.flujo_contrapartes` | L-V |
| 02:00 | `jobs.cashflow --today` | Mar-Sáb |

## Streamlit Dashboard — Vistas

Nav principal: **Mercado · Opciones · Portfolios · Operaciones · AuM · Manager**

Manager visible solo para emails en `MANAGER_EMAILS`. Determinado por header `Cf-Access-Authenticated-User-Email` de Cloudflare Access.

| Vista | Sub-tabs | Descripción |
|---|---|---|
| Mercado | Mercado · Libro · Curvas · Breakevens · Forwards · Retorno Total · Volúmenes | Microstructure, VWAP, volumen intraday; Libro en tiempo real (run_every=2s); curvas, breakevens (sub-tabs Tiempo Real · Histórico · Gráfico · Simulador), forwards, retorno total. Tab Mercado usa `@st.fragment(run_every=30)`. |
| Opciones | Mercado · Estrategias | Cadena GGAL con SPOT/VR/ADR/Tasa RF + volatility smile. Estrategias: spreads pre-configurados con payoff y costo histórico. |
| Portfolios | Reportes | Informe ejecutivo mensual por cuenta. Lee `Valuaciones.Carteras` (mes actual) y `Valuaciones.CarterasII` (mes anterior). Ver sección abajo. |
| Operaciones | Cash Flow · Contrapartes · Análisis · Flujo vs AuM | Cash Flow: `CashFlow.Movimientos`, filtro "Todas / Sin accionistas / Solo accionistas / Solo cooperativas". Contrapartes: filtros SEGMENTO+MONEDA, flujo mensual + drill-down. Análisis: Individual/Comparativo. Flujo vs AuM: gráfico dual para segmento=Fondos. |
| AuM | FCI · Análisis SG · Tasa Fija · CER | FCI: snapshot por fecha + evolución + detalle por soc. gerente. Análisis SG: Individual o Comparativo base 100. Tasa Fija y CER: toggle "Valor Nominal" alterna columna entre `cantidad` (VN) y `valuacion` (P×Q). |
| Manager | Diagnóstico · Backfills · Validaciones · Logs · Historial · Setup · Latencia | Solo admins. Backfills, upserts a Assets/Contrapartes, flujo inline, audit log en `Manager.ChangeLog`. Tab Latencia: benchmark en tiempo real de todas las queries MongoDB del dashboard (ms, docs, ms/doc). |

## Lógica de Negocio (consolidada)

Resumen de todas las reglas de dominio aplicadas en el dashboard. Implementación en `dashboard/services/` y, cuando es inevitable, inline en la view.

### 1. Valuación AuM

Fórmula por tipo de instrumento (aplicada por `services/portfolios.py::_calcular_valuacion` y dentro de `jobs/aum.py`):

- **Renta fija** (`Títulos Públicos`, `Letras`, `ONs`, `Fideicomisos`, `CPD`) → `cantidad × precio / 100` (precio cotiza sobre 100 de VN).
- **FCI / OTROS** → `cantidad × precio` directo.
- **Futuros** (en `jobs/aum.py`) → `(precio + 1) × cantidad`.

Constante: `TIPOS_DIVISOR_100 = {Títulos Públicos, Letras, ONs, Fideicomisos, CPD}`.

### 2. Cash Flow — filtros (`services/operaciones.py::filtrar_movimientos`)

Orden de aplicación:
1. **Rango fecha**: `fecha.dt.date` entre `rango[0]` y `rango[1]`.
2. **Monedas**: `unidad ∈ monedas_sel` (p.ej. `["ARS", "USD"]`).
3. **Filtro accionistas** (`filtro_acc`):
   - `"Todas"` → sin filtro; `seleccion` opcional filtra por `cuenta`.
   - `"Sin accionistas"` → cuenta **no** está en `CashFlow.Accionistas`.
   - `"Solo accionistas"` → cuenta está en `Accionistas`; dropdown permite elegir accionista específico (agrupa varios comitentes bajo un mismo nombre legal).
   - `"Solo cooperativas"` → cuenta **no** es accionista Y matchea regex `\bcoop` (case-insensitive). `es_cooperativa()`.

### 3. Portfolios Reportes — dataflow

```
repos.portfolios.get_carteras()        →  mes actual  (último snapshot Aunesa)
repos.portfolios.get_carteras_ii()     →  mes anterior (snapshot manual primer día hábil)
repos.portfolios.get_assets()          →  metadata por unidad
repos.portfolios.get_dolar_oficial()   →  A3500 (Trading.DOLAR)
repos.portfolios.get_valor_mep()       →  MEP (Valuaciones.Dolar)

services.portfolios.build_carteras_enriquecidas()
  → merge Carteras ⟵ Assets por 'unidad'
  → columna 'valuación' = P×Q (FCI/OTROS) o P×Q/100 (renta fija)

services.portfolios.build_carteras_ii_enriquecidas()
  → merge CarterasII ⟵ Assets (valuacion viene pre-calc del snapshot)

views.portfolios → donut + tablas + KPIs
```

### 4. AuM FCI — pipeline

```
repos.aum.get_aum_fci_agg()    ← Valuaciones.AuMResumenFCI (rollup, 1 doc/fecha)
repos.aum.get_aum_fci_snapshot(fecha)  ← Valuaciones.AuM raw (drill-down)
repos.aum.get_fci_unidades()   ← Assets.CARTERA=='CARTERA FCI'
```

El rollup AuMResumenFCI es un trade-off: el job `aum_resumen_fci.py` pre-materializa la suma por `(fecha, unidad)` para que el chart histórico transfiera ~22 docs en vez de los ~2.4k raw.

### 5. AuM Tasa Fija / CER — join chain

Tasa Fija: `Trading.Curvas` (`curva=='tasa_fija'`) → `ticker_corto` → `Valuaciones.Assets` (`TICKER==ticker_corto`) → `unidad` → `Valuaciones.AuM`.
CER: idem con `curva=='cer'`.

Para agregar un nuevo instrumento: insertar doc en `Trading.Curvas` con `curva: "tasa_fija"` (o `"cer"`) + doc en `Valuaciones.Assets` con `TICKER == ticker_corto`.

### 6. Flujo vs AuM (tab Operaciones)

`CashFlow.Contrapartes` (`segmento=="Fondos"`) → lista de fondos → `Valuaciones.Assets` (`CARTERA=="CARTERA FCI"`, `EMISOR ∈ fondos`) → `unidad` → `Valuaciones.AuM` (con `$group` por `(unidad, fecha_snapshot)` server-side).
En paralelo: `CashFlow.Flujo` filtrado por `contraparte ∈ fondos` y `moneda=="ARS"`.

Gráfico dual: barras verde/rojo (flujo por signo, eje izq) + línea naranja con forward-fill del AuM (eje der, `zero=False`).

### 7. Breakevens

Fórmulas (`engines/breakevens.py`):
- `retorno = (1+TEM)^(días/30) - 1`
- `inflacion = (1+retorno) × (paridad/100) - 1`
- `breakeven = (1+inflacion)^(30/días) - 1`

Emparejamiento: cada Lecap con el CER de vencimiento más cercano (máx 60 días de diferencia).

### 8. Simulador Breakevens (Mercado → Breakevens → Simulador)

P&L relativo **CER vs Lecap** por par, bajo escenarios de inflación mensual flat:
- **Settlement T+1** (próximo día hábil); **CER liq** = settlement − 10 días hábiles.
- `ret_lecap = flujo_vencimiento / precio_lecap − 1` (cierto).
- Para cada flujo pendiente del CER: `CER_proy = cer_liq × (1 + infl)^meses` → `flujo_pesos = monto_VN × CER_proy / cer_emision`.
- `ret_cer = Σ flujo_pesos / precio_cer − 1` → `P&L = ret_cer − ret_lecap` (bps, verde/rojo).
- El BE mensual calculado por `engines/breakevens.py` debería caer entre los dos escenarios donde el P&L cambia de signo.

### 9. Forwards

Fórmula: `((1 + TEA_B)^t_B / (1 + TEA_A)^t_A)^(1/(t_B - t_A)) - 1`.
Requiere TEA en TimeSales (escrito por `engines/curvas.py`, lag ~5s aceptable).
Matriz NxN por curva re-escrita cada 30s en `Trading.ForwardsLive`; snapshot diario en `Trading.ForwardsHistorico`.

### 10. Mercado → Tab Libro

Auto-refresh cada 2s via `@st.fragment(run_every=2)`.

- **Header**: selector de ticker (izq) | última actualización (der)
- **Fila 1**: Depth (book top 5) + Hourly Vol · Tape · Quant Analytics
- **Fila 2**: Last Minutes chart · Volume Profile

**Last Minutes chart**: eje X temporal real (`:T`, `%H:%M:%S`). VWAP horizontal verde (`strokeDash=[6,3]`). Toggle TEA alterna eje Y entre Precio y TEA (oculta VWAP en modo TEA).

**Volume Profile**: query desde medianoche UTC. Tick size dinámico (~25 barras). Solo niveles con operaciones reales.

### 11. Portfolios → Tab Reportes

Informe ejecutivo mensual por cuenta. Selector de cuenta en el header. Secciones:

#### 1. Resumen Ejecutivo
- KPIs: fecha, valor MEP, valor A3500 (de `Valuaciones.Dolar` y `Trading.DOLAR`), valuación ARS/A3500/USD total.
- Donut chart + tablas mes actual y mes anterior por cartera (ARS / DL / HD / FCI).
- **Mes actual**: `Valuaciones.Carteras` (último snapshot Aunesa, campo `valuacion` recalculado con regla 1).
- **Mes anterior**: `Valuaciones.CarterasII` (snapshot manual primer día hábil mes anterior, sincronizado al final de `jobs/aum.py` y `jobs/aum_backfill.py`).

#### 2. Carteras vs Benchmarks (4 gráficos — rendimiento acumulado mensual)

| Gráfico | Fuente cartera | Benchmarks | Estado |
|---|---|---|---|
| Cartera Total ARS vs Benchmarks | `Valuaciones.Rendimientos.rendimiento_ars` | A3500, Inflacion, Badlar | **Pendiente conectar** |
| Cartera Total USD | `Valuaciones.Rendimientos.rendimiento_usd` | — | **Pendiente conectar** |
| Cartera Pesos vs Benchmarks | `Valuaciones.Rendimientos.rendimiento_carteraars` | Badlar, Inflacion | **Pendiente conectar** |
| Cartera Dolar Linked USD | — | — | **Dummy — pendiente** |

#### Colecciones de soporte (carga manual por ahora)

**`Valuaciones.Benchmarks`** — una fila por `(periodo, benchmark)`:
```
{ periodo: "ago-25", benchmark: "Badlar", mensual: 0.033, acumulado: 0.057 }
{ periodo: "ago-25", benchmark: "A3500",  mensual: 0.132, acumulado: 0.204 }
{ periodo: "ago-25", benchmark: "Inflacion", mensual: 0.027, acumulado: 0.091 }
```
- `benchmark` ∈ `{"Badlar", "A3500", "Inflacion"}` (inflación mensual, NO el índice CER).
- `periodo`: `"mmm-aa"` (ej. `"ago-25"`).
- Los gráficos usan `acumulado` como eje Y.

**`Valuaciones.Rendimientos`** — una fila por `(id_cuenta, periodo)`:
```
{ id_cuenta: "1234", periodo: "ago-25",
  rendimiento_ars: 0.041, rendimiento_usd: 0.018, rendimiento_carteraars: 0.052 }
```
- `(valor_actual / valor_anterior) − 1`. Carga manual de momento; a futuro automatizado post-cierre mensual.

#### 3–7. Variaciones del mes y detalle de activos — **Dummy (pendiente conectar)**

## REST API (`api/`)

FastAPI (v0.1.0) expone datos de las colecciones MongoDB en endpoints REST/JSON. Sin autenticación por ahora (solo localhost); planeado: Cloudflare Access + API Keys.

```bash
uvicorn api.main:app --reload --port 8000   # dev
# Swagger UI: http://localhost:8000/docs
```

### Estructura

```
api/
├── main.py                # FastAPI app + health endpoint
├── deps.py                # get_db_cuentas() → CuentasAPI, get_db_operaciones() → OperacionesAPI
└── routers/
    ├── cuentas.py         # /api/cuentas/*
    └── operaciones.py     # /api/operaciones/*
```

Cada router usa `get_mongo_client_read()` (read-only, igual que el dashboard).

### Endpoints

| Método | Ruta | Colección API | Query params |
|---|---|---|---|
| GET | `/api/health` | — | — |
| GET | `/api/cuentas/accionistas` | `CuentasAPI.AccionistasAPI` | — |
| GET | `/api/cuentas/contrapartes` | `CuentasAPI.ContrapartesAPI` | — |
| GET | `/api/operaciones/flujo` | `OperacionesAPI.MesaAPI` | `contraparte`, `moneda`, `segmento`, `desde`, `hasta` |
| GET | `/api/operaciones/flujos` | `OperacionesAPI.FlujosAPI` | `cuenta`, `unidad`, `desde`, `hasta` |
| GET | `/api/carteras/` | `CarterasAPI.CarterasAPI` | `id_cuenta`, `unidad` |

### Patrón de migraciones (colecciones API)

Las colecciones originales (`CashFlow.*`, `Valuaciones.*`, etc.) son la **fuente de verdad**, actualizadas automáticamente por engines y jobs. Las colecciones API son **copias derivadas** con campos renombrados, limpiados o descartados, optimizadas para consumo externo.

**Flujo para agregar un nuevo endpoint:**
1. Identificar la colección origen y definir el mapping de campos (renombrar, descartar, transformar).
2. Agregar la función de migración en `scripts/api_migrate.py` (patrón: `drop()` + `insert_many()`, idempotente).
3. Crear el router/endpoint en `api/routers/`.
4. Agregar la nueva DB helper en `api/deps.py` si es una DB nueva.
5. Documentar el mapping en `docs/API_MIGRATIONS.md`.
6. Documentar el endpoint en `docs/API.md`.

**Re-sincronización:** `python -m scripts.api_migrate <comando>`. Todos los comandos son idempotentes (drop + insert).

### Colecciones API actuales

| Origen | Destino | Campos renombrados | Comando migrate |
|---|---|---|---|
| `CashFlow.Accionistas` | `CuentasAPI.AccionistasAPI` | cuenta→cuenta+id_cuenta+nombre, accionista→grupo | `accionistas` + `mover` |
| `CashFlow.Contrapartes` | `CuentasAPI.ContrapartesAPI` | denominacion→cuenta, cuenta→id_cuenta, contraparte→nombre, segmento→grupo | `contrapartes` + `mover` |
| `CashFlow.Flujo` | `OperacionesAPI.MesaAPI` | instrumento→unidad, cuenta→id_cuenta | `flujo` |
| `CashFlow.Movimientos` | `OperacionesAPI.FlujosAPI` | comprobante→boleto, fecha→concertacion (dd/mm→YYYY-MM-DD), total→bruto | `movimientos` |
| `Valuaciones.Carteras` | `CarterasAPI.CarterasAPI` | timestamp truncado a fecha (sin hora), descarta `actualizado` | `carteras` |

### Esquema unificado Cuentas (AccionistasAPI / ContrapartesAPI)

Ambas colecciones comparten: `cuenta` (str, original), `id_cuenta` (str, numérico), `nombre` (str), `grupo` (str).

### Testing

`python -m scripts.test_api [URL]` — prueba los 7 endpoints con filtros de ejemplo.

## Colecciones de referencia

### Trading
- **`CER`** — Serie BCRA id=30. `fecha`, `valor`.
- **`TAMAR`** — BCRA id=44.
- **`DOLAR`** — A3500, BCRA id=5.
- **`BADLAR`** — BCRA id=7.
- **`Curvas`** — Definición estática de renta fija. `ticker`, `ticker_corto`, `tipo`, `curva` (tasa_fija/cer), `fecha_vencimiento`, `fecha_emision`, `flujo_vencimiento`, `valor_nominal`, `cupon_anual`, `cer_emision`, `flujos[]`. Cargada manualmente.
- **`DiasHabiles`** — Calendario hábil argentino. Generado por `jobs/dias_habiles.py` una vez por año.

### Valuaciones
- **`Carteras`** — Último snapshot Aunesa por cuenta. Clave `(id_cuenta, unidad)`.
- **`CarterasII`** — Snapshot manual primer día hábil del mes anterior. Sincronizado al final de `jobs/aum.py` y `jobs/aum_backfill.py`. Campo `valuacion` pre-calculado.
- **`AuM`** — Snapshot diario por `(id_cuenta, unidad, fecha_snapshot)`.
- **`AuMResumenFCI`** — Rollup 1 doc por `fecha_snapshot` con `unidades[].{unidad, valuacion_total}`. Alimentado por `jobs/aum_resumen_fci.py`.
- **`Assets`** — Metadata de activos por `unidad`. Campos: `CARTERA`, `EMISOR`, `TICKER`, `CLASE_ACTIVO`, `CALIFICACION`, `VENCIMIENTO`. Unique index en `unidad`.
- **`Dolar`** — Snapshot MEP intradía. `timestamp`, `mep`, etc.
- **`Benchmarks`** — carga manual. `(periodo, benchmark)` → `mensual`, `acumulado`.
- **`Rendimientos`** — carga manual. `(id_cuenta, periodo)` → tres rendimientos.

### CashFlow.Flujo

Campos: `boleto`, `concertacion`, `tipoOperacion`, `cuenta`, `denominacion`, `instrumento`, `condiciones`, `bruto`, `segmento`, `contraparte`, `moneda`.

- `segmento` = segmento de mercado Aunesa (ej: "SENEBI", "MAE"). Distinto del `segmento` de Contrapartes.
- `contraparte` sobreescrito con nombre de `CashFlow.Contrapartes`.
- `boleto` = clave única (partial index sobre `$type: int`).
- Tipos excluidos: "Concurrencia - Caución colocadora (Apertura/Cierre)", "Futuros Financieros - Compra/Venta".

### CashFlow.Contrapartes

- `contraparte`: clave de join con `Flujo.contraparte`.
- `cuenta`: número en Aunesa (int, string, o CUIT). Puede haber múltiples docs por contraparte.
- `denominacion`: nombre legal.
- `segmento`: "Fondos" / "ALYC" / "Bancos". Asignado por `jobs/segmento_contrapartes.py`.

### CashFlow.Accionistas y Cooperativas

- **`CashFlow.Accionistas`**: colección manual con `{cuenta, accionista}`. Permite consolidar múltiples comitentes de un mismo accionista bajo un nombre único (ej. varios comitentes → "LA SEGUNDA"). Usada en el filtro de Cash Flow.
- **Cooperativas**: sin colección propia. Auto-detectadas en runtime por regex `\bcoop` case-insensitive (`services/operaciones.py::es_cooperativa`), excluyendo cuentas que estén en `Accionistas`. Filtro "Solo cooperativas" en Cash Flow.

## MongoDB Índices

Definidos en `scripts/crear_indices.py` (idempotente, soporta `unique` + `partialFilterExpression`). Ejecutar en servidor nuevo o al agregar colecciones (`python -m scripts.crear_indices`).

| Colección | Índice |
|---|---|
| `Trading.TimeSales` | `(ticker, timestamp)`, `(ticker, duration, timestamp)`, `(ticker, TEA, timestamp)`, `(ticker, TEM, timestamp)`, `(ticker, paridad, timestamp)` |
| `Trading.MarketSnapshot` | `ticker` |
| `Trading.ForwardsHistorico` | `(curva, fecha)` |
| `Trading.BreakevensHistorico` | `fecha` |
| `Trading.CER` | `fecha` |
| `Trading.Curvas` | `curva`, `ticker_corto` |
| `Valuaciones.AuM` | `fecha_snapshot`, `(unidad, fecha_snapshot)`, `(id_cuenta, fecha_snapshot)` |
| `Valuaciones.AuMResumen` | `(id_cuenta, unidad, fecha_snapshot)`, `(CARTERA, fecha_snapshot)`, `(EMISOR, fecha_snapshot)` |
| `Valuaciones.Carteras` | `(id_cuenta, unidad)` |
| `Valuaciones.Dolar` | `timestamp` |
| `Valuaciones.Assets` | `unidad` **(unique)**, `(EMISOR, CARTERA)` |
| `CashFlow.Flujo` | `(contraparte, moneda)`, `concertacion`, `boleto` **(unique, partial: `$type: int`)** |
| `CashFlow.Movimientos` | `fecha` |
| `Opciones.DataHistorica` | `fecha`, `(symbol, fecha)` |

## Notas técnicas importantes

- **MongoClient**: nunca llamar `client.close()`. Es un singleton compartido; cerrarlo mata el pool de Streamlit y tira `InvalidOperation` en todas las queries subsiguientes.
- **Dos clientes MongoDB**: `get_mongo_client()` (read-write) para motores y Manager. `get_mongo_client_read()` (read-only, `SECONDARY_PREFERRED`) para todas las vistas del dashboard. Ambos usan `serverSelectionTimeoutMS=30000` para tolerar elecciones en el cluster M10. Fallback a `MONGO_URI` si `MONGO_URI_READ` no está definido.
- **Regla repos/services/views**: toda query nueva a Mongo del dashboard va en `repos/<vista>.py`; toda lógica nueva sobre DataFrames en `services/<vista>.py`; la view solo orquesta. No regresar al patrón de queries inline.
- **`@st.cache_resource` para objetos DB**: `dashboard/shared/db.py` usa `cache_resource` porque `Database` no es serializable. Los loaders usan `cache_data`.
- **`parallel(*fns)` (`repos/aum.py`)**: ejecuta loaders cacheados sin args con `ThreadPoolExecutor`. Seguro con `@st.cache_data` (es thread-safe). Se usa para solapar 3–4 queries independientes en la vista AuM.
- **Cloudflare Access header**: `Cf-Access-Authenticated-User-Email` — solo presente cuando el request pasa por Cloudflare Access. En local el header no existe y `MANAGER_EMAILS` vacío permite acceso.
- **Altair v4 pie labels**: usar `mark_text(radius=N, color="white")` dentro del arco.
- **Altair eje X duplicado en barras mensuales**: usar `strftime` para agrupar como string + encoding `:O` con `sort=` explícito.
- **Altair fontWeight**: entero (`fontWeight=600`), no string.
- **Valuación AuM**: recalculada en la vista/services. `TIPOS_DIVISOR_100 = {Títulos Públicos, Letras, ONs, Fideicomisos, CPD}`.
- **CashFlow DB**: se llama `CashFlow` (sin espacio). Depósitos positivos, extracciones negativas.
- **Enriquecimiento CER**: el CER usado depende de la fecha de settlement del trade (T-10 días hábiles). Si un bono no opera un día, su último trade enriquecido puede usar el CER de ayer.
- **En el servidor**: siempre usar `/root/TradingAV/venv/bin/python`.
- **Ejecución siempre desde la raíz**: todos los entrypoints usan `python -m <módulo>` con `cwd=/root/TradingAV`. Ejecutar `python engines/valores.py` falla porque `core`, `jobs`, etc. no son discoverables con el working dir en `engines/`.
- **CI ruff**: GitHub Actions corre `ruff check` en cada push. Los errores más comunes en este repo: `F401` (import no usado — típicamente queda después de mover código a repos/services) y `F821` (nombre no definido — suele ser un `datetime`/`timedelta` que se removió al limpiar imports).
