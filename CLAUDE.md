# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

TradingAV — plataforma quant MERVAL/ROFEX. pyRofex WS → MongoDB Atlas M10 → FastAPI (`api.acaquant.com`) → **acaquant-web** Next.js en Vercel (`trading.acaquant.com`). Server en `/root/TradingAV` (Droplet DO), venv en `/root/TradingAV/venv`.

## Reglas que rompen todo si se olvidan

- **`python -m <módulo>` desde la raíz siempre**. `python engines/x.py` falla (`core` no es discoverable).
- **Nunca `client.close()` sobre los Mongo singletons** — mata el pool. Son 2: `core.mongo.get_mongo_client()` (rw, motores/crons) y `get_mongo_client_read()` (ro, `SECONDARY_PREFERRED`, usado por la API).
- **Atlas pausado 04:00–11:20 UTC diario** (ahorro). Durante la ventana la API devuelve error de conexión — no es bug.
- **Regla de capas**: `core/` no importa nada del proyecto. `engines/` y `jobs/` usan `core/` + `quant/`. `api/services/` es puro (sin FastAPI), `api/routers/` solo HTTP plumbing.
- **RBAC**: código nuevo usa `require_module(m)`, no `require_manager` (alias legacy).
- **Commits**: estilo `feat/fix/docs/refactor(scope): mensaje` en español, como el `git log`.

## Estructura

```
core/        # infra (mongo, websocket, rofex_session, roles, byma, mae)
engines/     # motores WS → Mongo (always-on L-V 13-20 UTC)
jobs/        # batch/cron
quant/       # cálculo puro (black_scholes, stats)
api/services # lógica pura (invocada por routers y por el agente)
api/routers  # thin HTTP wrappers. manager/ es paquete de sub-routers
api/agent/   # asistente tool-use (Claude/Gemini)
scripts/     # one-shot / migraciones / smoke
deploy/      # systemd + crontab.txt (fuente de verdad)
.claude/     # commands (/smoke /perf /seed-roles /deploy) + skills (add-bono add-endpoint debug-motor)
docs/        # API.md, ARCHITECTURE.md, ASISTENTE.md, API_MIGRATIONS.md
```

## Comandos

```bash
uvicorn api.main:app --reload --port 8000
python -m engines.<motor> | jobs.<job> | scripts.<cmd>
python -m scripts.api_migrate <cmd>            # resync colecciones *API.*API
python -m scripts.seed_roles [--dry]           # bootstrap RBAC
ruff check . [--fix]                           # line-length=100, py312
pytest -ra                                     # unit (pytest -m integration = requiere Atlas up)
python -m scripts.perf_scan [--strict]         # anti-patterns Mongo
```

CI (`.github/workflows/ci.yml`): ruff + perf_scan + pytest en cada push. Setup: Python 3.12, Node 20, Next 15.

## Trading.Curvas — shape de flujos (CRÍTICO, no inferible)

- **CER**: porcentual. `amortizacion_pct` + `cupon_sobre_residual` YA resuelto (NO re-multiplicar por `residual_previo_pct`). `cupon_anual=0` si zero coupon. Requiere `cer_emision`.
- **tasa_fija**: absolutos. `amortizacion` + `interes`. Requiere `flujo_vencimiento`.
- **soberanos** (`tipo='globales'|'bonares'`): mismo shape que CER, `cupon_sobre_residual` ya en USD.

Agregar instrumento: doc en `Trading.Curvas` + doc en `Valuaciones.Assets` con `TICKER == ticker_corto`. Sin el segundo no aparece en AuM/Portfolios.

`config.TICKERS_EXTRA_PRECIOS`: tickers que `motor_rofex` suscribe pero `motor_curvas` ignora. Default `['MERV - XMEV - AL30C - 24hs']` para `/api/analitica/canje`.

## RBAC

Cloudflare Access = quién entra. `core/roles.py` = qué ve.

Módulos: `home, renta-fija, derivados, estrategia, operaciones, portfolios, asistente, manager`.

| Módulo | admin | trader | sales |
|---|---|---|---|
| home / renta-fija / derivados / estrategia | ✓ | ✓ | ✓ |
| operaciones / portfolios / asistente | ✓ | ✓ | – |
| manager | ✓ | – | – |

Colecciones `Manager.{Users, RoleMatrix, RoleAudit}`. Helpers: `get_user_role`, `has_access`, `require_module(m)` (dependency). Cache TTL 60s → `invalidate_cache()` post-mutación. Matriz editable desde `/manager → ROLES Y PERMISOS`.

`GET /api/me` → `{email, role, modules, is_admin}`. Lo consume el frontend para filtrar nav + `src/proxy.ts` para redirects. Fallback: `MANAGER_EMAILS` env (legacy) → `DEFAULT_ROLE="sales"`.

## Fórmulas no inferibles

**AuM** (`jobs/aum.py`, `api/routers/carteras.py::_valuacion_api`):
- Renta fija (`Títulos Públicos, Letras, ONs, Fideicomisos, CPD`) → `cantidad × precio / 100`
- FCI / OTROS → `cantidad × precio`
- Futuros → `(precio + 1) × cantidad`

**Breakevens** (`engines/breakevens.py`, método Buscar Objetivo, cupón cero):
```
retorno_lecap = flujo_vto_lecap / precio_lecap − 1
X = [(1 + retorno_lecap) · (precio_cer · cer_emision) / (vn_cer · cer_actual)]^(1/meses) − 1
```
Match **mismo vto** Lecap↔CER (`MAX_DIFF_DIAS=20`). Anualización con `dias_cer` = vto − 10 hábiles. Filtro `mes_inflacion ≤ último IPC publicado`. Fallback Fisher si faltan datos.

**Forwards**: `((1 + TEA_B)^t_B / (1 + TEA_A)^t_A)^(1/(t_B − t_A)) − 1`. Requiere TEA en TimeSales (la escribe `motor_curvas`).

**AuM join chain**: `Trading.Curvas.curva` → `ticker_corto` → `Valuaciones.Assets.TICKER` → `unidad` → `Valuaciones.AuM`.

**Enriquecimiento CER**: `motor_curvas` usa CER con settlement T-10 hábiles. Si un bono no opera un día, el último trade puede quedar con CER de ayer.

## Asistente

Doc completo: `docs/ASISTENTE.md`. `api/agent/` + `POST /api/chat`. Provider Claude con router Haiku/Sonnet (`LLM_PROVIDER=claude`), Gemini fallback. Tools invocan `api/services/*` directamente. `BLOCKED_PATH_PREFIXES` = portfolio/operaciones/cuentas/manager (policy — no modificar sin coordinar). Editables sin deploy: `docs/asistente/estrategia.md` + `estrategias.md` (releen al cambiar mtime).

## Deploy

Push a `main` → Vercel auto-deploya acaquant-web. Backend: `git pull` + `systemctl restart api.service` en el Droplet, o skill `/deploy`. Motores de mercado los controla cron (start/stop L-V). Cron fuente de verdad: `deploy/crontab.txt`. Colecciones `*API.*API` se re-sync via `jobs/sync_api_copies.py` encadenado post-job fuente; manual con `scripts.api_migrate <cmd>`.

Jobs críticos diarios: `jobs.bcra --today` (22 UTC L-V, pide hoy+21d para CER forward), `jobs.argentina_datos` (12 UTC, RiesgoPais/IPC/REM), `jobs.dolar_api` (*/5 13-20 L-V, `Valuaciones.DolarOficial`), `jobs.aum` (23 L-V).
