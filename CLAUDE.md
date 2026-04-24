# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

TradingAV — plataforma quant de mercados argentinos (MERVAL/ROFEX). Motores en tiempo real vía pyRofex → MongoDB Atlas M10 → FastAPI (`api.acaquant.com`) → **acaquant-web** Next.js en Vercel (`trading.acaquant.com`).

**Backend**: Droplet DO, `/root/TradingAV`, venv en `/root/TradingAV/venv`.
**Auth**: Cloudflare Access (OTP por email) + Bearer `API_KEY` + CF service token.

## Estructura

```
core/       # infra (mongo, websocket, rofex_session, roles, byma, mae)
engines/    # motores always-on (WS → Mongo)
jobs/       # batch/cron
quant/      # cálculo puro (black_scholes, stats)
api/
  services/ # lógica pura (invocada por routers y por el agente)
  routers/  # HTTP thin wrappers. manager/ es paquete con sub-routers
  agent/    # asistente IA tool-use
scripts/    # one-shot / migraciones / smoke
deploy/     # systemd + crontab.txt (fuente de verdad del cron)
docs/       # API.md, ARCHITECTURE.md, ASISTENTE.md, asistente/
```

**Regla de capas**: `core/` no importa nada del proyecto. `engines/` y `jobs/` usan `core/` + `quant/`. `api/` usa `core.mongo.get_mongo_client_read()` (read-only). `api/services/` son funciones puras — `api/routers/` solo HTTP plumbing, cero lógica.

## Comandos

```bash
uvicorn api.main:app --reload --port 8000     # API
python -m engines.<motor>                      # motores
python -m jobs.<job>                           # batch
python -m scripts.api_migrate <cmd>            # resync colecciones API derivadas
python -m scripts.seed_roles [--dry]           # bootstrap RBAC
ruff check . [--fix]                           # line-length=100, py312
pytest -ra                                     # unit (pytest -m integration para tests que pegan a Mongo)
python -m scripts.perf_scan [--strict]         # anti-patterns Mongo
python -m scripts.crear_indices                # idempotente
```

**Siempre desde la raíz con `python -m <módulo>`** — `python engines/valores.py` falla porque `core` no es discoverable. En server: `/root/TradingAV/venv/bin/python`. CI: ruff + perf_scan + pytest en cada push.

## Mongo

2 singletons en `core.mongo`: `get_mongo_client()` (rw, motores + crons) y `get_mongo_client_read()` (ro, `SECONDARY_PREFERRED`, usado por API). **Nunca llamar `.close()`** — mata el pool.

**Atlas M10 pausado** 04:00–11:20 UTC diario para ahorro. Durante la ventana la API devuelve error de conexión (by design).

Índices en `scripts/crear_indices.py` — tocar ahí al agregar collection nueva.

## Engines

Todos tienen `update_price(ticker, data)` invocado por el WS en cada tick. Motores de mercado corren L-V 13:00–20:05 UTC (cron start/stop).

- `valores` — `Trading.TimeSales` + `MarketSnapshot` (microestructura).
- `options` — `Opciones.OptionsSnapshot` (BS Greeks, IV).
- `curvas` — enriquece `TimeSales` con `TEA/TEM/duration/convexity/paridad`. Loop 5s. Si `calcular_campos` retorna None marca `duration: null` (anti loop infinito).
- `forwards` / `breakevens` — matrices/pares cada 30s.
- `caucion` / `futuros_dlr` — snapshots 5s.
- `dolares` — `Valuaciones.DolarSnapshot` (MEP/CCL/canje live via AL30/D/C).
- `dolar_mep` — cron cada 15 min L-V para histórico.

## Trading.Curvas — shape de flujos (CRÍTICO)

- **CER**: porcentual. `amortizacion_pct` + `cupon_sobre_residual` ya resuelto (NO re-multiplicar por `residual_previo_pct`). `cupon_anual=0` si zero coupon. Requiere `cer_emision`.
- **tasa_fija**: absolutos. `amortizacion` + `interes`. Requiere `flujo_vencimiento`.
- **soberanos** (`tipo='globales'|'bonares'`): mismo shape que CER, `cupon_sobre_residual` ya en USD.

Para agregar instrumento: doc en `Trading.Curvas` + doc en `Valuaciones.Assets` con `TICKER == ticker_corto`. Sin esto, no aparece en AuM/Portfolios.

`config.TICKERS_EXTRA_PRECIOS`: tickers que `motor_rofex` suscribe live pero `motor_curvas` ignora (no enriquece TEA/duration). Default `['MERV - XMEV - AL30C - 24hs']` para alimentar `/api/analitica/canje`.

## RBAC

Cloudflare decide **quién entra**; `core/roles.py` decide **qué ve**.

Módulos canónicos (hardcoded en `MODULES`): `home, renta-fija, derivados, estrategia, operaciones, portfolios, asistente, manager`.

Matriz default (editable desde `/manager → ROLES Y PERMISOS`):

| Módulo | admin | trader | sales |
|---|---|---|---|
| home / renta-fija / derivados / estrategia | ✓ | ✓ | ✓ |
| operaciones / portfolios / asistente | ✓ | ✓ | – |
| manager | ✓ | – | – |

Colecciones `Manager.{Users, RoleMatrix, RoleAudit}`. Helpers: `get_user_role`, `has_access`, `require_module(m)` (dependency FastAPI). Cache TTL 60s + `invalidate_cache()` post-mutación.

`GET /api/me` devuelve `{email, role, modules, is_admin}` — lo consume el frontend para filtrar nav y el `src/proxy.ts` de acaquant-web para decidir redirects.

Fallback: email sin doc en `Users` → `MANAGER_EMAILS` env (legacy admin) → `DEFAULT_ROLE="sales"`.

## Lógica de negocio (no inferible del código)

### Valuación AuM (`jobs/aum.py`, `api/routers/carteras.py::_valuacion_api`)

- Renta fija (`Títulos Públicos`, `Letras`, `ONs`, `Fideicomisos`, `CPD`) → `cantidad × precio / 100`
- FCI / OTROS → `cantidad × precio`
- Futuros → `(precio + 1) × cantidad`

### Breakevens CER/Lecap (`engines/breakevens.py`)

**Método activo — Buscar Objetivo** (cupón cero, sin TEM/paridad):

```
retorno_lecap = flujo_vto_lecap / precio_lecap − 1
cer_vto(X)    = cer_actual · (1 + X)^meses_pendientes
retorno_cer(X)= (vn_cer · cer_vto(X) / cer_emision) / precio_cer − 1
⇒ X = [(1 + retorno_lecap) · (precio_cer · cer_emision) / (vn_cer · cer_actual)]^(1/meses_pendientes) − 1
```

Fallback Fisher clásico si faltan datos. Anualización usa `dias_cer` (vto − 10 hábiles), **no** días al vto. Match **mismo vto** Lecap↔CER (`MAX_DIFF_DIAS=20`). Filtro `MIN_DIAS_PLAZO=50` y `mes_inflacion ≤ último IPC publicado`. Flag `cer_fijado` en `Trading.Curvas` cuando el CER de liquidación ya se publicó forward.

Debug: `GET /api/manager/checks/breakevens`.

### Forwards

`((1 + TEA_B)^t_B / (1 + TEA_A)^t_A)^(1/(t_B − t_A)) − 1`. Requiere TEA en TimeSales (escrita por `motor_curvas`).

### AuM Tasa Fija / CER — join chain

`Trading.Curvas.curva` → `ticker_corto` → `Valuaciones.Assets.TICKER` → `unidad` → `Valuaciones.AuM`. Server-side `$group`.

### Flujo vs AuM

`CashFlow.Contrapartes.segmento="Fondos"` → `Valuaciones.Assets.CARTERA="CARTERA FCI"` → `unidad` → `Valuaciones.AuM`. Paralelo: `CashFlow.Flujo` filtrado `contraparte ∈ fondos`, `moneda="ARS"`.

## Jobs & Crons

Fuente de verdad: `deploy/crontab.txt`. Aplicar: `crontab /root/TradingAV/deploy/crontab.txt`.

Críticos:
- `jobs.bcra --today` (22 UTC L-V) — pide hoy+21d para capturar CER forward.
- `jobs.argentina_datos` (12 UTC diario) — RiesgoPais / IPC / REM INDEC.
- `jobs.dolar_api` (*/5 13-20 L-V) — `Valuaciones.DolarOficial`; consumido por `engines.futuros_dlr` (tasa implícita vs mayorista).
- `jobs.aum` (23 L-V) + sync API — Carteras+CarterasII+AuM.
- `jobs.carteras` (3×/día L-V) + sync API.
- Atlas `pause`/`resume` (04:00 / 11:20 UTC).

Colecciones `*API.*API` son copias derivadas optimizadas. Resync: `scripts.api_migrate <cmd>` o `jobs.sync_api_copies` (encadenado en cron). Ver `docs/API_MIGRATIONS.md`.

## Asistente

Doc completo: `docs/ASISTENTE.md`. Resumen:
- `api/agent/` + `POST /api/chat`. Provider **Claude** router Haiku/Sonnet (`LLM_PROVIDER=claude`), Gemini fallback.
- Runner provider-agnostic (`MAX_STEPS=6`). Tools invocan `api/services/*` directamente (sin HTTP loopback).
- Bloqueadas por policy: portfolio/operaciones/cuentas/manager.
- Observabilidad: `Manager.AsistenteLogs` + `Manager.IntelDocs`.
- Editables sin deploy: `docs/asistente/estrategia.md`, `docs/asistente/estrategias.md` (releen al cambiar mtime).

## Frontend (acaquant-web)

Repo sibling, Next.js 15 + React 19 + Tailwind 4. Lightweight Charts (trading-style) + Recharts (resto).

- Páginas server-side (`async` + `Promise.all`). `src/lib/api.ts::apiFetch()` inyecta Bearer + CF service token + ISR TTL.
- Cada `src/app/api/*/route.ts` proxea al backend (oculta `API_KEY` del browser).
- `src/proxy.ts` (Next 16+, NO `middleware.ts`) consulta `/api/me` y redirige por módulo.
- Admin UI en `/manager`: tabs USUARIOS (CRUD) y ROLES Y PERMISOS (matriz + audit log).

## Notas

- **CER enriquecimiento**: el CER usado por `motor_curvas` depende del settlement del trade (T-10 hábiles). Si un bono no opera un día, el último trade puede usar CER de ayer.
- **CashFlow DB**: nombre `CashFlow` (sin espacio). Depósitos positivos, extracciones negativas.
- `require_manager` queda como alias de `require_module("manager")` por compat; código nuevo usar `require_module(m)` directo.
