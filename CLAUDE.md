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
api/mcp/     # MCP server (FastMCP) + OAuth 2.1 provider + discovery
scripts/     # one-shot / migraciones / smoke
deploy/      # systemd + crontab.txt (fuente de verdad)
.claude/     # commands (/smoke /perf /seed-roles /deploy /motor-status) + skills (add-bono add-endpoint add-job debug-motor)
docs/        # API.md, ASISTENTE.md, API_MIGRATIONS.md
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

**Forwards**: `((1 + TEA_B)^t_B / (1 + TEA_A)^t_A)^(1/(t_B − t_A)) − 1`. Lee última TEA por ticker desde `MarketSnapshot.metrics.TEA` (escrita por `motor_curvas` en cada update). Igual patrón usan `breakevens.py` y los services de portfolio/renta-fija. **No leer TimeSales agregado** — es estrictamente más caro y devuelve el mismo valor que el snapshot live.

**TC Breakeven** (`api/services/renta_fija.py::_tc_breakeven`, sólo tasa fija nativa o CER fijado): `TC_BE = MEP × (flujo_vencimiento / precio_actual)`. Lee `flujo_vencimiento` de `Trading.Curvas`, `last_price` del trade más reciente y MEP de `get_ultimo_mep` (live, TTL 5s). Se calcula on-the-fly en `get_renta_fija` y `listar_curva` — no se persiste.

**AuM join chain**: `Trading.Curvas.curva` → `ticker_corto` → `Valuaciones.Assets.TICKER` → `unidad` → `Valuaciones.AuM`.

**Enriquecimiento CER**: `motor_curvas` usa CER con settlement T-10 hábiles. Si un bono no opera un día, el último trade puede quedar con CER de ayer.

## Asistente

Doc completo: `docs/ASISTENTE.md`. `api/agent/` + `POST /api/chat`. Provider Claude con router Haiku/Sonnet (`LLM_PROVIDER=claude`), Gemini fallback. Tools invocan `api/services/*` directamente. `BLOCKED_PATH_PREFIXES` = portfolio/operaciones/cuentas/manager (policy — no modificar sin coordinar). Editables sin deploy: `docs/asistente/estrategia.md` + `estrategias.md` (releen al cambiar mtime).

## MCP server (Custom Connector)

`api/mcp/` montado en `https://api.acaquant.com/mcp` — 34 tools de SOLO LECTURA sobre datos de mercado (curvas, forwards, breakevens, opciones, REM, macro, descomposición, sensibilidad, order book L2 live + histórico). NO expone portfolio/operaciones/cuentas/AuM/manager (mismo policy que el asistente). Cada tool es thin wrapper sobre `api/services/*`. Cliente principal: Claude Desktop / claude.ai vía Custom Connector. Doc completo de cada tool: `docs/MCP_TOOLS.md`.

**Auth**: OAuth 2.1 + PKCE + DCR (RFC 7591), Cloudflare Access como IdP. Flow: Claude hace DCR → `/oauth/authorize` (CF Access pide login al user) → handler lee `cf-access-jwt-assertion` → emite `code` → `/oauth/token` lo canjea por JWT (HS256, TTL 1h) → Claude usa el JWT en Bearer en `/mcp/`. Storage en Mongo db `MCP` (TTL automático en codes/tokens).

Configurable: `MCP_BEARER_TOKEN` (static, fallback dev/curl), `MCP_JWT_SECRET` (firma JWTs OAuth), `MCP_OAUTH_ISSUER` (default `https://api.acaquant.com`). Sin ninguno de los dos, `/mcp` queda deshabilitado.

**Dos cosas críticas que rompen el connector** (se aprendieron a los golpes; doc completo en memoria `project_mcp_cf_access.md`):

1. **CF Access path scoping**. App `acaquant-mcp-bypass` (BYPASS + Everyone) cubre 5 paths: `/mcp`, `/oauth/token`, `/oauth/register`, `/.well-known/oauth-protected-resource`, `/.well-known/oauth-authorization-server`. Si CF Access tapa `/mcp`, el cliente recibe HTML de login en vez de 401 → muere silencioso. `/oauth/authorize` SÍ debe estar protegido (ahí logea el user). 5/5 destinations al tope.
2. **`TransportSecuritySettings` en `api/mcp/server.py`** con `allowed_hosts` (`api.acaquant.com`) y `allowed_origins` (`https://claude.ai`, `https://claude.com`). El default del SDK MCP solo acepta localhost → 421 Misdirected Request. El smoke local NO replica esta condición.

Smoke: `python -m scripts.mcp_smoke_oauth` (auto-emite JWT y pega a `/mcp/` local — valida middleware bearer + tools, no condiciones de prod).

## Deploy

Push a `main` → Vercel auto-deploya acaquant-web. Backend: `git pull` + `systemctl restart api.service` en el Droplet, o skill `/deploy`. Motores de mercado los controla cron (start/stop L-V). Cron fuente de verdad: `deploy/crontab.txt`. Colecciones `*API.*API` se re-sync via `jobs/sync_api_copies.py` encadenado post-job fuente; manual con `scripts.api_migrate <cmd>`.

**Frontend en repo hermano `../acaquant-web/`** (Next.js, deploy auto en Vercel). Cambios de API con impacto en UI se editan ahí con rutas absolutas — no es submodule, es checkout paralelo.

Jobs críticos diarios: `jobs.bcra --today` (22 UTC L-V, pide hoy+21d para CER forward), `jobs.argentina_datos` (12 UTC, RiesgoPais/IPC/REM), `jobs.aum` (23 L-V), `jobs.cleanup_curvas` + `jobs.cleanup_futuros_dlr` (12:30 UTC L-V, antes de motores), `jobs.snapshot_cierre` (20:25 UTC L-V, post-cierre — lee `MarketSnapshot` y persiste cierre por bono en `Trading.SnapshotsCierre`), `jobs.negocio_movimientos` (cada hora 15-22 UTC L-V, pega a Aunesa `consolidadosGenerales`, parsea/categoriza/agrupa por boleto y persiste idempotente en `CashFlow.NegocioMovimientos` para la vista `/operaciones/negocio`).

Dólar oficial: única fuente es `Valuaciones.DolarOficialLive` (feed MAE mayorista UST$T plazo 000, script local en PC oficina). Histórico (anchors 7d/MTD/YTD del watchlist `/argy`) deshabilitado hasta que MAE acumule >30 días — antes venía de `Valuaciones.DolarOficial`/dolarapi.com, eliminado el 2026-05-04. Para series macro (`serie_macro` con `dolar_oficial`/`dolar_mayorista`) usar `Trading.DOLAR` (BCRA A3500 fixing diario).

## Patrón de escritura a `Trading.MarketSnapshot`

Dos motores escriben a `MarketSnapshot` con `$set` parcial sin pisarse:
- `engines/valores.py` (motor_rofex) → `book.bids/offers`, `metrics.{last_price, open_price, high_price, low_price, closing_price, vwap, total_nominals}`, `updated_at`. Refresh 1s.
- `engines/curvas.py` (motor_curvas) → `metrics.{TEA, TEM, duration, mod_duration, convexity, paridad}`. Refresh 5s.

Cada uno escribe SOLO sus campos via `UpdateOne($set: dot-notation, upsert=True)`. **No usar `ReplaceOne`** — pisa los campos del otro motor. El doc no tiene `top_trades` ni `recent_trades` (eran payload muerto, removidos).

## Order book L2 (captura full)

Motor dedicado `engines/order_book_l2.py` — sesión rofex separada del motor_rofex, suscribe solo entries `BIDS+OFFERS` con depth 5 para los tickers en `config.TICKERS_BOOK_FULL`. Cada cambio del book persistido como doc nuevo (append-only) en `Trading.OrderBookL2` (Time Series Collection). Sin pisado de TimeSales/MarketSnapshot. Cron L-V 13:00–20:05 UTC vía `motor_order_book_l2.service`. Setup one-shot: `python -m scripts.init_orderbook_l2_collection`.

## Motor de Valuaciones (PnL Títulos)

Doc dedicado: **`docs/MOTOR_VALUACIONES.md`** — leer antes de tocar `api/services/pnl.py`.

`/aum → VALUACIONES → PNL TÍTULOS` calcula PnL por (cuenta, ticker) con cost-basis weighted-average. Tres KPIs separados: realizado / no-realizado / pasivo (cupones+divs+amorts). Endpoint `GET /api/portfolio/pnl?id_cuenta=X`.

**Reglas críticas:**
- `pnl_no_realizado = valor_aum − costo_remanente`. NO usar `qty × precio_actual` — el precio del AuM viene en paridad cruda (sin /100 para bonos).
- Mapping `unidad ↔ ticker` viene de `Valuaciones.Assets` UPPERCASE (campo `TICKER`), no de regex sobre la unidad.
- Cada boleto en `NegocioMovimientos` tiene `mep` snapshot inmutable. Pesificación = `importe × b.mep`. Fallback a `_mep.get_mep_for_date()` solo si `mep=null`.
- Categorías de boleto que entran al cost-basis: `compra, venta, suscripcion_fci, rescate_fci, acreencia`. `comision` (avales/custodia) se ignora — el `importe` ya viene neto.
- "Licitación" del primario se categoriza como `compra` (`aunesa_negocio.py::categorizar` con `_normalizar` que saca tildes).

## Filtros de exclusión del AuM

`jobs/_aum_filters.py` define qué se excluye al persistir el snapshot diario:

1. `unidad == "USDL"` (cash USD link).
2. `cuenta` o `unidad` con `OTC` o `CDC` (case-insensitive substring).
3. `id_cuenta` ∈ `CuentasAPI.ContrapartesAPI.id_cuenta` (FCI / sociedades gerentes — match por id no por nombre, formatos difieren).
4. `cuenta` contiene como palabra completa un nombre de `CashFlow.Contrapartes.contraparte` (ADCAP, LOMBARD, etc. — `\bNOMBRE\b` case-insensitive). Cubre cuentas que se escapan de la regla 3.
5. `unidad == "ARS"` para `[100]` y `[101]` (cash de cuentas propias).

Aplica al cron diario (`jobs/aum.py`) y al cleanup retroactivo (`scripts/cleanup_aum_excluidos.py`). Las reglas 3 y 4 viven en BD — el equipo edita Contrapartes y se respeta solo en el próximo run.

**Cuenta 255 (ACA Valores Intermediación)** además se excluye SOLO de la **vista** AuM (no de la persistencia) — `_EXCLUDED_FROM_AUM_VIEW = {"255"}` en `api/services/portfolio.py`. La cuenta sigue capturándose para verla individualmente en `/aum → VALUACIONES`.

## Filtros de cuenta en endpoints

`api/services/_cuentas_filter.py::match_cuenta_filter(filtro)` devuelve sub-doc `$match` para filtrar pipelines Mongo por tipo:
- `todas`, `accionistas` (∈ `CuentasAPI.AccionistasAPI`), `sin_accionistas`, `cooperativas` (∉ accionistas + regex `\bcoop`), `productores` (∈ `CashFlow.Productores`).

Lo usan operaciones (vista negocio) y portfolio (AuM por cartera, FCI, total, diff). VALID_FILTERS único — sumar tipos nuevos en un solo lugar.

## Patrón "live fallback" (cierre persistido + live de hoy)

Endpoints que sirven data agregada del cierre diario y aceptan `fecha` como input deben leer `Trading.SnapshotsCierre` primero y, si no hay doc para hoy (cron `jobs.snapshot_cierre` corre 20:25 UTC), caer a `Trading.MarketSnapshot.metrics`. Mismos campos, mismo shape.

Sin esto, durante horario de mercado las vistas se quedan en el cierre del día anterior hábil hasta que el cron corra a las 17:25 ART. Con esto, `fecha=hoy` siempre devuelve datos vigentes.

Aplicado hoy en (commits 2026-05-04):
- `api/services/analitica.py::snapshot_curva_historico` — fallback A para `fecha=today`.
- `api/services/renta_fija.py::get_historico_curva` — agrega fila por ticker desde MarketSnapshot si hoy no está en SnapshotsCierre.
- `api/services/carry_trade.py::_precios_diarios_curva` — helper `_precios_live_curva` para el último punto.

Si en el futuro se agregan endpoints similares (canje, breakevens, forwards diarios), aplicar el mismo patrón. **Frontend complementa**: las routes de Next que sirven estos endpoints necesitan `export const dynamic = "force-dynamic"` + `revalidate = 0` + `Cache-Control: no-store` para que el CDN de Vercel no cachee la respuesta y borre el "hoy" live.
