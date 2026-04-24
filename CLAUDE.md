# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TradingAV — plataforma cuantitativa de mercados argentinos (MERVAL/ROFEX). Streama datos en tiempo real, corre motores paralelos (microstructure, opciones, curvas, forwards, breakevens), persiste en MongoDB Atlas (M10), expone vía FastAPI consumida por **acaquant-web** (Next.js) en `trading.acaquant.com`.

## Acceso

| Capa | URL | Auth |
|---|---|---|
| Frontend | trading.acaquant.com | Cloudflare Access (email OTP) |
| REST API | api.acaquant.com | Bearer `API_KEY` + CF Access service token |
| Swagger | api.acaquant.com/docs | — |

**Backend**: Droplet DO, `/root/TradingAV`, venv en `/root/TradingAV/venv`. `cloudflared.service` always-on.
**Frontend**: Vercel (push a `main` → auto-deploy).

## Variables de entorno (`.env`)

```
ROFEX_USER / ROFEX_PASSWORD / ROFEX_ACCOUNT / ROFEX_API_URL / ROFEX_WS_URL
MONGO_URI                                ← read-write (motores + crons)
MONGO_URI_READ                           ← read-only (API)
AUNESA_CLIENT_ID / AUNESA_USERNAME / AUNESA_PASSWORD
MANAGER_EMAILS                           ← CSV admin legacy (también se seedea a Manager.Users)
API_KEY                                  ← Bearer API
ANTHROPIC_API_KEY / GEMINI_API_KEY
LLM_PROVIDER={claude|gemini}             ← default claude
FINNHUB_API_KEY
CF_ACCESS_TEAM / CF_ACCESS_AUD           ← validación JWT CF Access
CF_TRUSTED_SERVICE_TOKENS                ← common_names allow-list (acaquant-web SSR)
BYMA_CLIENT_ID / BYMA_CLIENT_SECRET / BYMA_TOKEN_URL / BYMA_BASE_URL
MAE_API_KEY / MAE_ENV={prod|uat}
ATLAS_PUBLIC_KEY / ATLAS_PRIVATE_KEY / ATLAS_PROJECT_ID / ATLAS_CLUSTER_NAME
```

`config.TICKERS_EXTRA_PRECIOS`: tickers que `motor_rofex` suscribe live pero `motor_curvas` ignora (no están en `Trading.Curvas`). Default: `['MERV - XMEV - AL30C - 24hs']` para alimentar `/api/analitica/canje`.

## Estructura

```
core/       # infra (mongo, websocket, rofex_session, byma, mae, roles)
engines/    # motores always-on (WS → Mongo)
jobs/       # batch/cron
quant/      # cálculo puro (black_scholes, stats)
api/
  main.py   # app + middlewares + includes
  auth.py   # CF JWT + require_module(m) + require_manager (legacy)
  services/ # lógica pura (sin FastAPI) — invocada por routers y por el agente
  routers/  # HTTP thin wrappers. manager/ es paquete con sub-routers
  agent/    # asistente IA tool-use (Claude/Gemini)
scripts/    # one-shot / migraciones / smoke tests
tests/      # pytest unit (no requieren Mongo)
deploy/     # systemd + crontab.txt
docs/       # API.md, ARCHITECTURE.md, ASISTENTE.md, asistente/*
```

**Regla de capas**: `core/` no importa nada del proyecto. `engines/` y `jobs/` usan `core/` + `quant/`. `api/` usa `core.mongo.get_mongo_client_read()` (read-only). `scripts/` libre.

## Comandos

```bash
# REST API
uvicorn api.main:app --reload --port 8000

# Motores (systemd los corre en prod; acá local)
python -m engines.valores / options / curvas / forwards / breakevens
python -m engines.caucion / futuros_dlr / dolares / dolar_mep

# Jobs batch (ver deploy/crontab.txt)
python -m jobs.aum                       # cron 23 UTC
python -m jobs.argentina_datos           # RiesgoPais / IPC / REM
python -m jobs.dolar_api                 # oficial/mayorista/blue
python -m jobs.bcra --today
python -m jobs.carteras / cashflow / flujo_contrapartes / options_rollup / ...

# Migraciones API (*API.*API)
python -m scripts.api_migrate <cmd>      # carteras, aum, assets, flujos-titulos, ...

# Roles (fase 1 RBAC)
python -m scripts.seed_roles [--dry] [--force]

# Test y lint
ruff check . [--fix]                     # line-length=100, py312, ignora E501
pytest -ra                               # unit (no requiere Mongo)
pytest -m integration                    # requiere Mongo
python -m scripts.perf_scan [--strict]   # anti-patterns Mongo
python -m scripts.test_api / test_chat   # smoke
python -m scripts.crear_indices          # idempotente
```

**Siempre desde la raíz con `python -m <módulo>`** — `python engines/valores.py` falla porque `core` no es discoverable. En el server: `/root/TradingAV/venv/bin/python`.

CI (`.github/workflows/ci.yml`): `ruff check` + `perf_scan` (informativo) + `pytest -ra` en cada push a `main`.

## Architecture

```
ROFEX WS (pyRofex)
  │
  ▼
core.websocket.WebSocketManager  (chunks 50 tickers, dispatch por motor)
  │
  ├─► engines.valores / options / curvas / forwards / breakevens / ...
  │
  ▼
MongoDB Atlas M10 (Trading, Opciones, Valuaciones, CashFlow, Manager)
  │
  ▼
FastAPI (api.acaquant.com)
  │
  ▼
acaquant-web Next.js (Vercel)
```

**MongoClient**: 2 singletons. `get_mongo_client()` (rw, motores + crons). `get_mongo_client_read()` (ro, `SECONDARY_PREFERRED`, usado por API; fallback a rw si `MONGO_URI_READ` no existe). `serverSelectionTimeoutMS=30000` para tolerar elecciones M10. **Nunca llamar `.close()`** — mata el pool.

### Engines

Cada motor tiene `update_price(ticker, data)` invocado por el WS en cada tick.

| Motor | Destino | Qué hace |
|---|---|---|
| `valores` | `Trading.TimeSales` + `MarketSnapshot` | Microestructura bonos/Lecaps/CER: trades + snapshot 1s |
| `options` | `Opciones.OptionsSnapshot` | GGAL: BS Greeks, IV Newton-Raphson |
| `curvas` | enriquece `Trading.TimeSales` | Agrega TEA/TEM/Duration/Convexity/Paridad a tasa_fija, CER y soberanos USD. Loop 5s. Si `calcular_campos` retorna None marca `duration: null` (anti-loop infinito) |
| `forwards` | `Trading.ForwardsLive/Historico` | Matriz NxN tasas forward por curva, cada 30s |
| `breakevens` | `Trading.BreakevensLive/Historico` | BE inflación mensual CER/Lecap cada 30s |
| `caucion` | `Trading.CaucionSnapshot` + `Caucion` | ARS + USD, plazo del próximo día hábil |
| `futuros_dlr` | `Trading.FuturosDLRSnapshot` + `FuturosDLR` | Outrights DLR con TNA implícita vs mayorista |
| `dolares` | `Valuaciones.DolarSnapshot` | MEP/CCL/canje live via AL30/AL30D/AL30C |
| `dolar_mep` | `Valuaciones.Dolar` | Cron histórico (cada 15 min L-V) |

### Trading.TimeSales

Base: `ticker`, `timestamp`, `price`, `size`, `side` (BUY/SELL/MID), `money`.

Enriquecidos por `engines/curvas.py` (solo tickers en `Trading.Curvas`):

| Campo | Instrumentos | Descripción |
|---|---|---|
| `duration` | todos | Macaulay en años |
| `convexity` | tasa_fija + cer | 2da derivada precio/yield |
| `TEA` | tasa_fija + cer | Tasa efectiva anual |
| `TEM` | tasa_fija | Tasa efectiva mensual |
| `paridad` | cer + soberanos | precio / técnico × 100 |

### Trading.Curvas — flujos

- **CER**: porcentual. `amortizacion_pct`, `cupon_sobre_residual` (YA resuelto, NO re-multiplicar por residual_previo_pct), `cupon_anual=0` si zero coupon.
- **tasa_fija**: absolutos. `amortizacion` + `interes`.
- **soberanos**: mismo shape que CER, `cupon_sobre_residual` ya en USD.

Bonos hard-dollar seedeados (`curva='soberanos'`): Globales NY (GD29D/GD30D/GD35D/GD38D/GD41D, `tipo='globales'`), Bonares AR (AE38D/AL29D/AL30D/AL35D/AL41D/AN29D/AO27D/AO28D, `tipo='bonares'`).

## Jobs batch

Fuente de verdad del cron: `deploy/crontab.txt`. Aplicar con `crontab /root/TradingAV/deploy/crontab.txt`.

Críticos:

- Motores L-V: start 13:00 UTC, stop 20:05 UTC.
- `jobs.argentina_datos` (12:00 diario): RiesgoPais + IPC mensual/interanual + REM (IPC INDEC).
- `jobs.dolar_api` (*/5 13-20 L-V): oficial/mayorista/blue → `Valuaciones.DolarOficial`. Consumido por `engines.futuros_dlr` (tasa implícita vs mayorista).
- `jobs.bcra --today` (22:00 L-V): pide hoy+21d para capturar CER forward (habilita `cer_fijado=true`).
- `jobs.aum` (23:00 L-V): snapshot + CarterasII + sync API.
- `jobs.carteras` (11:35 / 14:00 / 16:00 L-V) + sync API.
- `jobs.flujo_contrapartes` (22:00 L-V) + sync API.
- `jobs.cashflow --today` (02:00 Mar-Sáb) + sync API.
- `jobs.options_rollup` (20:15 L-V): `Opciones.Data` → `DataHistorica`.
- Atlas pause/resume (04:00 / 11:20 UTC diario): ahorra ~31% compute. API devuelve error durante la ventana.

## Roles (RBAC — fase 1)

Cloudflare Access decide **quién entra**; `core/roles.py` decide **qué ve**.

**Módulos canónicos** (hardcoded en `core/roles.py::MODULES`):
`home, renta-fija, derivados, estrategia, operaciones, portfolios, asistente, manager`.

**Matriz default** (seedea a `Manager.RoleMatrix`, editable en runtime):

| Módulo | admin | trader | sales |
|---|---|---|---|
| home / renta-fija / derivados / estrategia | ✓ | ✓ | ✓ |
| operaciones / portfolios / asistente | ✓ | ✓ | – |
| manager | ✓ | – | – |

**Colecciones** (`Manager.*`): `Users` (email, role, enabled), `RoleMatrix` (role → modules), `RoleAudit` (append-only).

**API**: `core.roles.get_user_role(email)`, `has_access(email, module)`, `require_module(m)` (dependency FastAPI). Cache TTL 60s, `invalidate_cache()` post-mutación.

**Fallback**: email sin doc en `Users` → `MANAGER_EMAILS` env (admin legacy) → `DEFAULT_ROLE="sales"`.

**Estado actual**: Fase 1 deployada (infra + seed), pero `api/main.py` sigue usando `require_manager` legacy. Fase 2 pendiente: endpoints CRUD `/api/manager/users`, `/api/manager/roles`, `/api/me` + migración de routers a `require_module`.

## REST API

Catálogo completo en `docs/API.md` + Swagger. Patrones:

- **Cotizaciones live**: `/api/cotizaciones/{renta-fija, forwards, breakevens, caucion, futuros-dlr, argy, mep, opciones}`.
- **Histórico**: `/api/cotizaciones/historico/{forwards, breakevens, mep, trades, curva, caucion, futuros-dlr}`.
- **Analítica**: `/api/analitica/{listar-curva, serie-macro, clasificar-nivel, snapshot-curva-historico, pendiente-curva, liquidez-secundario, sensibilidad-retorno, canje, carry-trade, estrategia-historico}`.
- **Portfolio** (restringido): `/api/portfolio/*`, `/api/titulos/*`.
- **Operaciones** (restringido): `/api/operaciones/*`, `/api/cuentas/*`.
- **Asistente**: `POST /api/chat`.
- **Manager** (admin): `/api/manager/{status, jobs, checks, options, asistente, intel, logs}`.

Rate limits: `/api/chat` 30/min-500/día, `/api/manager/jobs/run` 5/h-20/día. Error model tipado: `{code, message, retryable, retry_after_s}`.

### Colecciones API derivadas

Las originales son fuente de verdad; `*API.*API` son copias optimizadas para consumo externo. Re-sincronización: `python -m scripts.api_migrate <cmd>` o automática vía `jobs/sync_api_copies.py` encadenado en cron.

| Origen | Destino | Comando |
|---|---|---|
| `CashFlow.Accionistas/Contrapartes/Flujo/Movimientos` | `CuentasAPI.*` / `OperacionesAPI.*` | `accionistas`, `contrapartes`, `flujo`, `movimientos` |
| `Valuaciones.Carteras/AuM/Assets` | `PortfolioAPI.*` / `TitulosAPI.AssetsAPI` | `carteras`, `aum`, `assets` |
| `Trading.Curvas + BondsMaster` | `TitulosAPI.ValuacionesAPI` | `flujos-titulos` |

## Asistente de mesa

Doc completo: `docs/ASISTENTE.md`. Resumen:

- Módulo `api/agent/` + `POST /api/chat` + vistas `/asistente`, `/manager:ASISTENTE`, `/manager:INTEL`.
- Provider: **Claude** con router Haiku/Sonnet (`LLM_PROVIDER=claude`). Gemini fallback legacy.
- Runner provider-agnostic (formato canónico Claude). `MAX_STEPS=6`.
- Tools invocan `api/services/*` directamente (sin HTTP loopback). Bloqueadas por policy: portfolio/operaciones/cuentas/manager (doble cinturón: filtro al declarar + dispatch).
- Observabilidad: `Manager.AsistenteLogs` (turnos), `Manager.IntelDocs` (reportes macro cargados).
- Editables sin deploy: `docs/asistente/estrategia.md`, `docs/asistente/estrategias.md` (releen al cambiar mtime).

## Frontend: acaquant-web

Repo sibling, Next.js 15 + React 19 + Tailwind 4. Lightweight Charts para trading-style, Recharts para el resto.

**Patrón**: páginas server-side (`async` + `Promise.all`), `src/lib/api.ts::apiFetch()` inyecta Bearer + CF service token + ISR TTL. Cada `src/app/api/*/route.ts` proxea al backend (oculta `API_KEY` del browser).

**Gating admin (Next 16+)**: `src/proxy.ts` (NO `middleware.ts` — tener ambos rompe el build) restringe `/manager`, `/asistente`, `/api/chat` a `MANAGER_EMAILS`. El email viene del header `cf-access-authenticated-user-email` inyectado por CF Access.

Comandos: `npm run dev` / `build` / `lint`.

## Lógica de Negocio (no inferible del código)

### 1. Valuación AuM

`jobs/aum.py` y `api/routers/carteras.py::_valuacion_api`:
- **Renta fija** (`Títulos Públicos`, `Letras`, `ONs`, `Fideicomisos`, `CPD`) → `cantidad × precio / 100`
- **FCI / OTROS** → `cantidad × precio`
- **Futuros** → `(precio + 1) × cantidad`

Constante: `TIPOS_DIVISOR_100 = {Títulos Públicos, Letras, ONs, Fideicomisos, CPD}`.

### 2. Breakevens CER/Lecap

`engines/breakevens.py` + `jobs/backfill_breakevens.py`. Tick 30s.

**Método activo — Buscar Objetivo** (cupón cero, sin TEM/paridad, evita compounding de convenciones):

```
retorno_lecap = flujo_vto_lecap / precio_lecap − 1
cer_vto(X)    = cer_actual · (1 + X)^meses_pendientes
retorno_cer(X)= (vn_cer · cer_vto(X) / cer_emision) / precio_cer − 1
⇒ X = [(1 + retorno_lecap) · (precio_cer · cer_emision) / (vn_cer · cer_actual)]^(1/meses_pendientes) − 1
```

Fallback a Fisher clásico si faltan precios o `cer_actual`: `retorno=(1+TEM)^(dias_cer/30)−1`, `inflacion=(1+retorno)·(paridad/100)−1`, `BE=(1+inflacion)^(30/dias_cer)−1`. Anualización usa **dias_cer** (vto − 10 hábiles), no días al vencimiento.

**Reglas**:
- Emparejamiento **mismo vto** Lecap ↔ CER (`MAX_DIFF_DIAS=20`). Dedup: si un CER aparece en varios pares, gana el de menor diff.
- `MIN_DIAS_PLAZO = 50`: descarta pares cuyo IPC ya salió o sale en <10 días.
- Filtro `mes_inflacion ≤ último IPC publicado` (lee `Trading.InflacionMensual`).
- Flag `cer_fijado` en `Trading.Curvas` cuando el CER de liquidación ya se publicó forward (seteado por `engines/curvas.py`).

Debug paso-a-paso: `GET /api/manager/checks/breakevens`.

### 3. Forwards

`((1 + TEA_B)^t_B / (1 + TEA_A)^t_A)^(1/(t_B − t_A)) − 1`. Requiere TEA en TimeSales (escrita por `engines/curvas.py`, lag ~5s OK). Matriz NxN re-escrita cada 30s en `ForwardsLive`; snapshot diario en `ForwardsHistorico`.

### 4. Simulador Breakevens (P&L CER vs Lecap)

Escenarios de inflación flat. Settlement T+1, CER liq = settlement − 10 hábiles.
- `ret_lecap = flujo_vto / precio_lecap − 1`
- `CER_proy = cer_liq · (1 + infl)^meses` → `flujo_pesos = VN · CER_proy / cer_emision`
- `ret_cer = Σ flujo_pesos / precio_cer − 1` → `P&L = ret_cer − ret_lecap` (bps)

### 5. Flujo vs AuM

`CashFlow.Contrapartes.segmento="Fondos"` → lista fondos → `Valuaciones.Assets.CARTERA="CARTERA FCI"` → `unidad` → `Valuaciones.AuM` (`$group` server-side). Paralelo: `CashFlow.Flujo` filtrado por `contraparte ∈ fondos`, `moneda="ARS"`.

### 6. AuM Tasa Fija / CER — join chain

`Trading.Curvas.curva='tasa_fija'` → `ticker_corto` → `Valuaciones.Assets.TICKER == ticker_corto` → `unidad` → `Valuaciones.AuM`. Idem `curva='cer'`.

Para agregar instrumento: doc en `Trading.Curvas` + doc en `Valuaciones.Assets` con matching TICKER.

### 7. Portfolios Reportes

- Mes actual: `Valuaciones.Carteras` (último Aunesa, valuación recalculada con regla 1).
- Mes anterior: `Valuaciones.CarterasII` (snapshot manual primer día hábil mes anterior, `valuacion` pre-calc).
- Benchmarks: `Valuaciones.Benchmarks` `(periodo, benchmark)` → `{mensual, acumulado}`. `benchmark ∈ {Badlar, A3500, Inflacion}`.
- Rendimientos: `Valuaciones.Rendimientos` `(id_cuenta, periodo)` (carga manual).

## Notas críticas

- **Enriquecimiento CER**: el CER usado por `engines/curvas.py` depende del settlement del trade (T-10 días hábiles). Si un bono no opera un día, el último trade puede usar CER de ayer.
- **CashFlow DB**: nombre `CashFlow` (sin espacio). Depósitos positivos, extracciones negativas.
- **Índices Mongo**: todos en `scripts/crear_indices.py` (idempotente). Tocar ahí al agregar collection nueva.
- **Atlas pausado** 04:00–11:20 UTC diario — API devuelve error de conexión durante la ventana.

## Deployment

Servidor: Droplet DO, `root@/root/TradingAV/`, venv en `/root/TradingAV/venv/`.

**Always-on**: `cloudflared.service`, `api.service` (uvicorn 127.0.0.1:8000).

**Motores de mercado** (L-V, horario rueda): `motor_{rofex,options,curvas,forwards,breakevens,caucion,futuros_dlr,dolares}.service`. Todos usan `/root/TradingAV/venv/bin/python -m engines.<nombre>` con `WorkingDirectory=/root/TradingAV`.
