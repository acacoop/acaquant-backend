# TradingAV API

**Version:** 0.1.0
**Base URL (prod):** `https://api.acaquant.com`
**Base URL (dev):** `http://127.0.0.1:8000`
**Protocol:** REST over HTTP/1.1 · JSON
**Repo:** [`api/`](../api/)

---

## 1. Overview

The TradingAV API is a FastAPI service that exposes the quantitative data layer of TradingAV — microstructure for fixed income and options on Argentine markets (MERVAL/ROFEX), portfolio analytics, cash flow, news and macro context.

It is the only consumption boundary of the platform: the production frontend (**acaquant-web**, Next.js on Vercel) proxies every read through it, and the trading-desk assistant (`/api/chat`) dispatches its tool calls against the same service layer used by the HTTP routers.

**Design principles**

- **Read-only by default.** All `GET` endpoints use a dedicated MongoDB user with `SECONDARY_PREFERRED` read preference. The two mutating endpoints (`PUT /api/cotizaciones/opciones/tasa`, `POST /api/manager/jobs/run`, admin `intel/*`) are explicit and gated.
- **Thin routers, fat services.** Router modules under `api/routers/` parse query params and delegate to pure-Python services under `api/services/`, which are the same units the assistant invokes without HTTP loopback.
- **Defense in depth.** Cloudflare Tunnel + Access (SSO), signed JWT validation, bearer key, rate limiting by identity, admin gating via email allow-list, and explicit SSRF protection on user-supplied URLs.
- **Snapshots over joins.** Heavy analytical endpoints read pre-aggregated API collections (`CuentasAPI`, `PortfolioAPI`, `OperacionesAPI`, `TitulosAPI`) kept in sync by scheduled jobs.

---

## 2. Quick start

```bash
# Local dev
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000

# Smoke test (spawns curl for every documented endpoint)
python -m scripts.test_api
python -m scripts.test_api http://192.168.1.100:8000

# Interactive exploration
open http://localhost:8000/docs      # Swagger UI
open http://localhost:8000/redoc     # ReDoc
```

Health probe (always unauthenticated):

```bash
curl https://api.acaquant.com/api/health
# → {"status":"ok"}
```

---

## 3. Authentication

The API runs behind three independent authentication layers. A request must pass all three to reach a handler.

### 3.1 Cloudflare Tunnel

The origin server has no public IP. All inbound traffic terminates at Cloudflare and is tunneled to the Droplet over `cloudflared.service`. Direct internet exposure is impossible.

### 3.2 Cloudflare Access (identity)

The public hostname `api.acaquant.com` sits behind a Zero Trust application. Two credential types are accepted:

1. **User SSO** — e-mail OTP login. Cloudflare injects the headers
   - `Cf-Access-Authenticated-User-Email`
   - `Cf-Access-Jwt-Assertion` (RS256, validated by the service).
2. **Service token** — used by the Next.js SSR layer of acaquant-web. Sent as
   - `CF-Access-Client-Id`
   - `CF-Access-Client-Secret`

The API validates the Cloudflare JWT against the Access JWKS endpoint (`https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`) with audience equal to the configured `CF_ACCESS_AUD`. An invalid or expired JWT yields `401 CF JWT inválido`.

Service tokens are recognized by presence of a `common_name` claim in the JWT. Only `common_name`s listed in `CF_TRUSTED_SERVICE_TOKENS` (env, comma-separated) are accepted; others are logged and rejected.

If `CF_ACCESS_TEAM` / `CF_ACCESS_AUD` are empty (development), JWT validation is skipped and the service falls back to the raw email header. Never deploy without these set.

### 3.3 Bearer API key

Every endpoint except `/api/health` requires:

```http
Authorization: Bearer <API_KEY>
```

`API_KEY` is a static secret shared between server (`.env`) and clients (acaquant-web `.env.local`, internal scripts). If `API_KEY` is empty, bearer check is disabled (dev only).

```bash
curl -H "Authorization: Bearer $API_KEY" https://api.acaquant.com/api/cotizaciones/mep
```

### 3.4 Admin gating

A subset of endpoints requires the caller's validated email to be in `MANAGER_EMAILS` (comma-separated). Service tokens in `CF_TRUSTED_SERVICE_TOKENS` are accepted as admin because acaquant-web performs the user-side gate in `src/proxy.ts` before proxying.

Admin-only routers:

| Prefix | Purpose |
|---|---|
| `/api/manager/*` | Engine/job status, checks, job runs, changelog, latency, assistant observability, intel ingest, options config |
| `/api/chat` | Trading-desk assistant |

Non-admin calls to these routes return `403 no autorizado`.

---

## 4. Rate limits

Applied globally via SlowAPI keyed by caller identity (`cf-access-jwt-assertion` → email → IP). Only the two most expensive endpoints set explicit quotas today; the rest are unbounded per user but still gated by auth.

| Endpoint | Limit |
|---|---|
| `POST /api/chat` | **30 / minute**, **500 / day** |
| `POST /api/manager/jobs/run` | **5 / hour**, **20 / day** |

Exceeding a limit returns `429` with a typed error body (§5).

---

## 5. Error model

FastAPI validation errors follow the default `422` shape (`{"detail":[{"loc":..., "msg":...}]}`).

Handler-level errors use one of two shapes:

**Simple:** `{"detail": "<message>"}` — used for 400/401/403/404/500 from standard handlers.

**Typed:** used by `/api/chat` and the global rate-limit handler:

```json
{
  "detail": {
    "code":          "rate_limit | transport | bad_response | llm_error | internal",
    "message":       "human-readable",
    "retryable":     true,
    "retry_after_s": 60
  }
}
```

`code` values:

| Code | HTTP | Meaning |
|---|---|---|
| `rate_limit` | 429 | Per-endpoint quota exceeded (typed handler `_rate_limit_handler`) or upstream LLM rate limit. Retry after `retry_after_s`. |
| `transport` | 503 | Could not reach LLM provider. |
| `bad_response` | 502 | LLM returned a malformed payload. |
| `llm_error` | 502 | Generic LLM-side error. |
| `internal` | 500 | Unhandled server error. Not retryable. |

---

## 6. Conventions

- **Path prefix.** Every route is under `/api`. Health is `/api/health`.
- **Date format.** `YYYY-MM-DD` for calendar days, ISO-8601 with `+00:00` for timestamps. Legacy `DD/MM/YYYY` only appears as stored form in `CashFlow.Movimientos` and is normalized on write.
- **Currency.** `ARS` or `USD` in the `moneda`/`unidad` field.
- **Filters.** All query params are optional unless marked required. Missing filters return the full collection (subject to projection).
- **Response envelopes.** List endpoints return a JSON array. Analytical endpoints return an object with the minimal shape described per route.
- **Projection.** Responses are projected server-side to remove `_id` and reduce payload. Field lists are authoritative in this document; clients must tolerate new fields.
- **Compression.** Responses ≥ 1 KiB are served with `Content-Encoding: gzip` when the client advertises it (`GZipMiddleware`).
- **Cache.** Hot reads are cached in-process by the service layer (TTL ranges from 5 s for market snapshots up to 3600 s for BCRA series). Mutating endpoints (`PUT`/`POST`/`PATCH`/`DELETE`) clear or bypass cache.
- **CORS.** Not enabled. The only public consumer is acaquant-web, which calls the API server-side through its proxy routes.

---

## 7. Endpoint reference

Routes are grouped by tag. Access column:

- **pub** — requires Cloudflare Access + Bearer.
- **adm** — additionally requires `email ∈ MANAGER_EMAILS` or trusted service token.

### 7.1 Health

| Method | Path | Access | Description |
|---|---|---|---|
| GET | `/api/health` | — | Liveness probe. No auth. |

**Response** `{"status":"ok"}`.

---

### 7.2 Cotizaciones (`/api/cotizaciones/*`)

Live and historical market data. All reads go directly against `Trading.*` / `Opciones.*` / `Valuaciones.*`; no migration needed.

| Method | Path | Access | Summary |
|---|---|---|---|
| GET | `/badlar` | pub | BADLAR series (BCRA id=7) |
| GET | `/cer` | pub | CER series (BCRA id=30) |
| GET | `/dolar` | pub | Official USD (A3500, BCRA id=5) |
| GET | `/mep` | pub | Latest MEP snapshot |
| GET | `/forwards` | pub | Live forward rate matrix |
| GET | `/breakevens` | pub | Live breakeven inflation |
| GET | `/renta-fija` | pub | Fixed-income market snapshot |
| GET | `/opciones` | pub | Options chain with Greeks |
| GET | `/opciones/meta` | pub | Risk-free rate + VR anchors |
| PUT | `/opciones/tasa` | pub | Update risk-free rate (`valor` 0<v<3). Clears cache. |
| GET | `/historico/mep` | pub | MEP series |
| GET | `/historico/forwards` | pub | Forward matrices by date |
| GET | `/historico/breakevens` | pub | Breakevens by date |
| GET | `/historico/trades` | pub | TimeSales, last 15 days, hard-limit 10 000 trades |
| GET | `/historico/opciones` | pub | Option trades, last 21 days, hard-limit 5 000 |
| GET | `/historico/curva` | pub | Daily close per ticker in a curve (`curva` required) |

#### Common query parameters

| Param | Type | Default | Notes |
|---|---|---|---|
| `desde` | `date` | — | `YYYY-MM-DD` inclusive |
| `hasta` | `date` | — | `YYYY-MM-DD` inclusive |
| `instrumento` | `string` | — | Short ticker (`TX26`) or full ROFEX (`MERV - XMEV - TX26 - 24hs`). Short tickers are resolved against `Trading.Curvas.ticker_corto` to hit the `(ticker, timestamp)` index. |
| `curva` | `enum` | — | `tasa_fija` \| `cer` \| `tamar` \| `soberanos` \| `dolar_linked` |
| `tipo` | `enum` | — | `CALL` \| `PUT` (options) |

#### Example — last MEP

```bash
curl -H "Authorization: Bearer $API_KEY" \
     https://api.acaquant.com/api/cotizaciones/mep
```

```json
{ "mep": 1410.8578, "timestamp": "2026-03-25T11:00:02.854000+00:00" }
```

#### Example — renta fija snapshot

```bash
curl -H "Authorization: Bearer $API_KEY" \
     "https://api.acaquant.com/api/cotizaciones/renta-fija?instrumento=TX26"
```

```json
[
  {
    "instrumento": "MERV - XMEV - TX26 - 24hs",
    "book": { "bids": [...], "offers": [...] },
    "metrics": {
      "total_nominals": 12500000,
      "vwap": 98.42,
      "last_price": 98.55,
      "open_price": 97.9,
      "high_price": 98.8,
      "low_price": 97.65,
      "closing_price": 97.8
    },
    "recent_trades": [ { "timestamp": "...", "price": 98.55, "size": 100000, "side": "BUY", "money": 98550000 } ]
  }
]
```

#### Enriched TimeSales fields

`/historico/trades` and `/historico/curva` surface fields written by `engines/curvas.py` for instruments in `Trading.Curvas`:

| Field | Instruments | Meaning |
|---|---|---|
| `duration` | all | Macaulay duration (years) |
| `TEA` | `tasa_fija` + `cer` | Annual effective rate |
| `TEM` | `tasa_fija` | Monthly effective rate |
| `paridad` | `cer` | `price / (VN × CER_trade / CER_emision) × 100` |

---

### 7.3 Analítica (`/api/analitica/*`)

HTTP projection of the assistant's Tier 1 tools. Same functions (`listar_curva`, `obtener_serie_macro`, `clasificar_nivel`) that the assistant invokes directly through the service registry.

| Method | Path | Access | Summary |
|---|---|---|---|
| GET | `/listar-curva` | pub | Enriched bond table per curve |
| GET | `/serie-macro` | pub | Series for a macro variable or `<TICKER>.<FIELD>` |
| GET | `/clasificar-nivel` | pub | Percentile classification vs window |

#### `GET /listar-curva`

| Param | Type | Required | Notes |
|---|---|---|---|
| `curva` | enum | yes | `cer` \| `tasa_fija` \| `tamar` \| `soberanos` \| `dolar_linked` |
| `ordenar_por` | enum | no | `vencimiento` (default) \| `volumen_dia` \| `tea` \| `duration` |
| `vencimiento_min_meses` | float | no | Horizon lower bound (months) |
| `vencimiento_max_meses` | float | no | Horizon upper bound (months) |
| `limit` | int | no | Top-N after sort |

Each element: `ticker`, `ticker_corto`, `tipo`, `fecha_vencimiento`, `fecha_emision`, `meses_al_vto`, `ultimo_precio`, `tea`, `tem`, `paridad`, `duration`, `total_money_dia`, `total_nominals_dia`, `ts_ultimo_trade`.

#### `GET /serie-macro`

`variable` accepts keyword aliases (`tamar`, `cer`, `dolar`, `badlar`, `mep`, `ccl`, `canje`, `ipc`, `ipim`, `riesgo_pais`, `repo`, `rem_inflacion`) or a `<TICKER>.<FIELD>` reference. `ventana_dias` bounded to `[1, 3650]` (default 90).

---

### 7.4 Cuentas (`/api/cuentas/*`)

| Method | Path | Access | Summary |
|---|---|---|---|
| GET | `/accionistas` | pub | Shareholder accounts (manual in `CashFlow.Accionistas`) |
| GET | `/contrapartes` | pub | Counterparty accounts, `grupo ∈ {Fondos, ALYC, Bancos}` |

Shared schema: `cuenta`, `id_cuenta`, `nombre`, `grupo`.

---

### 7.5 Operaciones (`/api/operaciones/*`)

| Method | Path | Access | Summary |
|---|---|---|---|
| GET | `/flujo` | pub | Trade flow (per-boleto) from `OperacionesAPI.MesaAPI` |
| GET | `/flujos` | pub | Cash movements from `OperacionesAPI.FlujosAPI` |
| GET | `/fondos` | pub | Counterparties `grupo=Fondos` with at least one FCI asset |
| GET | `/flujo-vs-aum` | pub | Monthly flow (bars) vs AuM (line) for a fund |

#### `/flujo` — query

`contraparte`, `moneda` (`ARS|USD`), `segmento` (`SENEBI|MAE|…`), `desde`, `hasta`.

Fields: `boleto`, `concertacion`, `tipoOperacion`, `cuenta`, `denominacion`, `unidad`, `bruto`, `segmento`, `contraparte`, `moneda`.

#### `/flujos` — query

`cuenta` (format `[N] NAME`), `unidad` (`ARS|USD`), `desde`, `hasta`.

Fields: `boleto`, `concertacion`, `cuenta`, `informacion`, `bruto`, `unidad`. `bruto > 0` = deposit, `< 0` = withdrawal.

#### `/flujo-vs-aum` — query

`contraparte` (required), `moneda` (default `ARS`). Returns `{contraparte, moneda, unidades[], aum[{mes,total}], flujo[{mes,bruto}]}` with monthly aggregation done server-side via `$group`.

---

### 7.6 Portfolio (`/api/portfolio/*`)

Reads from `PortfolioAPI.CarterasAPI`, `PortfolioAPI.AumAPI`, `TitulosAPI.AssetsAPI`, `TitulosAPI.ValuacionesAPI` and `Valuaciones.CarterasII`. Valuation formula is centralised in `_valuacion_api(cantidad, precio, cartera, clase)`:

- Fixed income (`Títulos Públicos`, `Letras`, `ONs`, `Fideicomisos`, `CPD`) → `cantidad × precio / 100`
- Futures → `(precio + 1) × cantidad`
- FCI / others → `cantidad × precio`

| Method | Path | Access | Summary |
|---|---|---|---|
| GET | `/carteras` | pub | Raw positions (current) |
| GET | `/aum` | pub | AuM snapshots (historical) |
| GET | `/resumen` | pub | Executive summary + per-CARTERA breakdown |
| GET | `/detalle` | pub | Per-position detail with share % |
| GET | `/tasa-fija` | pub | Tasa fija bucket from latest AuM snapshot |
| GET | `/cer` | pub | CER bucket from latest AuM snapshot |
| GET | `/fci-serie` | pub | FCI history (rollup + per-issuer split) |
| GET | `/fci-snapshot` | pub | FCI per-unit detail at a fecha |

#### `/carteras`

Query: `id_cuenta`, `unidad`.
Fields: `id_cuenta`, `unidad`, `cantidad`, `precio`, `timestamp`.

#### `/aum`

Query: `id_cuenta`, `unidad`, `cuenta`, `desde`, `hasta`, `ultimo` (bool).
When `ultimo=true`, other range filters are ignored.
Fields: `fecha`, `id_cuenta`, `unidad`, `cantidad`, `cuenta`, `precio`, `valuacion`.

#### `/resumen`

Query: `id_cuenta` (optional).
Without `id_cuenta` returns only the list of accounts. With `id_cuenta` returns `{cuentas, mes_actual: {CARTERA: valuacion}, mes_anterior: {CARTERA: valuacion}}`. `mes_actual` is valued live from `CarterasAPI`; `mes_anterior` uses the pre-calculated `Valuaciones.CarterasII`.

#### `/detalle`

Query: `id_cuenta` (required).
Returns `{posiciones[], total}` with each position including `unidad`, `ticker`, `emisor`, `clase_activo`, `cartera`, `calificacion`, `vencimiento`, `cantidad`, `precio`, `valuacion`, `pct`.

#### `/tasa-fija`

No params. Joins `AumAPI (last snapshot)` ∩ `AssetsAPI (clase_activo=FIJA)` ∩ `ValuacionesAPI (curva=tasa_fija)`. Returns per-ticker `valuacion`, `cantidad`, `cobro_proyectado` (= `cantidad × flujo_vencimiento / 100`) and a `cuentas[]` breakdown.

#### `/cer`

No params. Same join shape with `curva=cer` but without `cobro_proyectado` (depends on future CER). Adds `paridad` and `tea` from the latest enriched trade when available.

#### `/fci-serie` / `/fci-snapshot`

- `/fci-serie` — Query `desde`, `hasta`. Reads the daily rollup `Valuaciones.AuMResumenFCI` (≈22 docs/month) and enriches with `EMISOR` from `AssetsAPI`. Returns `[{fecha, total, por_emisor}]`, bounded to 730 rows.
- `/fci-snapshot` — Query `fecha` (required). Returns per-unit detail `[{unidad, emisor, ticker, cuenta, id_cuenta, valuacion, cantidad}]`.

---

### 7.7 Títulos (`/api/titulos/*`)

| Method | Path | Access | Summary |
|---|---|---|---|
| GET | `/assets` | pub | Instrument metadata |
| GET | `/flujos` | pub | Unified cash flows (`Trading.Curvas` + `Trading.BondsMaster`) |

Assets query: `unidad`, `ticker`, `cartera`, `emisor`, `clase_activo`.
Flujos query: `ticker`, `curva` (`tasa_fija|cer|""`), `moneda_flujo` (`ARS|USD`).

`flujos[]` element schema: `fecha`, `amortizacion` (% of VN for CER, absolute for bonds), `interes` (rate on residual for CER, absolute for bonds), `residual`.

---

### 7.8 News (`/api/news*`)

Aggregates local and global headlines (RSS + Finnhub) into `News.Headlines`. The reader mode (`/article`) fetches the target URL server-side; all URLs are validated against an anti-SSRF policy that blocks private, loopback, link-local, reserved and cloud metadata addresses, plus non-HTTP(S) schemes and non-80/443 ports.

| Method | Path | Access | Summary |
|---|---|---|---|
| GET | `/api/news` | pub | Paginated headlines |
| GET | `/api/news/article` | pub | Reader-mode extraction (`trafilatura`, 1 h in-memory cache) |
| GET | `/api/news/stats` | pub | Per-source counts over last `horas` hours |

Headlines query: `desde`, `hasta`, `fuente`, `categoria`, `keyword` (title substring), `limit` (≤500), `skip` (≤5000).

---

### 7.9 Market (`/api/market/*`)

Global watchlist + calendar + OHLC.

| Method | Path | Access | Summary |
|---|---|---|---|
| GET | `/quotes` | pub | Watchlist quotes with 7d/MTD/YTD/1Y returns derived from stored anchors |
| GET | `/calendar/economic` | pub | Finnhub economic calendar |
| GET | `/candle` | pub | OHLC via Yahoo Finance (Finnhub candles are paywalled on free tier) |
| GET | `/profile` | pub | Finnhub company profile |

`/quotes?symbols=AAPL,TSLA` filters; empty = all.
`/calendar/economic` takes `desde`, `hasta`, `importancia` (0–3), `country`, `limit` (≤1000).
`/candle` requires `symbol`, `resolution ∈ {1,5,15,30,60,D,W,M}`; `desde`/`hasta` default to [−365d, now]. Returns `{symbol, resolution, candles: [{t,o,h,l,c,v}]}`. If Yahoo is unreachable the handler returns `502` with `{"detail":"Yahoo: <message>"}`.

---

### 7.10 Manager (`/api/manager/*`) · admin

Operational tooling. Every route requires `MANAGER_EMAILS` membership (or trusted service token). All reads pass through `get_mongo_client_read()`; explicit mutations (`/intel/save`, `/intel/patch`, `/intel/delete`, `/options/expiries` PUT, `/jobs/run`) use the writer.

#### 7.10.1 Status

- `GET /status` — unified state of motors (`TimeSales`, `MarketSnapshot`, `ForwardsLive`, `BreakevensLive`, `OptionsSnapshot`) and batch jobs (CER, DOLAR, AuM, Carteras, Movimientos, Flujo). Staleness thresholds vary per probe; `estado ∈ {ok, lento, critico, fuera_rueda, sin_datos, atrasado, error_parse}`. Market hours = weekdays 10:00–17:05 ART.

#### 7.10.2 Checks

| Path | Description |
|---|---|
| `GET /checks/curvas-pendientes` | Trades in `TimeSales` missing `duration`, grouped by ticker |
| `GET /checks/forwards` | Per-curve TEA availability vs `ForwardsLive.tickers` |
| `GET /checks/cer` | CER used in the last enriched trade per CER bond |
| `GET /checks/tasa-fija` | State of tasa-fija instruments in the latest AuM snapshot |
| `GET /checks/debug-forward` | Step-by-step forward calculation (`tc_a`, `tc_b` required) |
| `GET /checks/tickers-curvas` | `ticker_corto` index of `Trading.Curvas` |

#### 7.10.3 Jobs

| Method | Path | Limit | Description |
|---|---|---|---|
| POST | `/jobs/run` | 5/h, 20/d | Spawn a job (`tipo ∈ aum_backfill, aum_resumen_fci, carteras, cashflow, flujo, bcra, sync_api_copies, crear_indices, cleanup_curvas`; `args[]` appended). Returns `{job_id}`; worker is a `subprocess.run` with 360 s timeout. |
| GET | `/jobs/{job_id}` | — | Current state: `status ∈ {running, done, error}`, `rc`, last 1500 chars of stdout/stderr |
| GET | `/jobs/history` | — | Runs from `Manager.JobRuns` (TTL 60 d). Query: `tipo`, `status`, `desde`, `hasta`, `limit≤500` |
| GET | `/jobs/history/stats` | — | Per-tipo aggregates since `desde` (default 7 d): `total`, `ok`, `partial`, `error`, `last_run`, `last_status` |

Route ordering matters: `/jobs/history` and `/jobs/history/stats` are declared before the catch-all `/jobs/{job_id}`.

#### 7.10.4 Options config

| Method | Path | Description |
|---|---|---|
| GET | `/options/expiries` | `{disponibles, activos, auto_pick, actualizado, mapa_size}` from `Opciones.Metadata` |
| PUT | `/options/expiries` | Body `{expiries: string[8]}`; empty → auto-pick mode. Engine reloads on next 5 min tick. |

#### 7.10.5 Changelog & latency

- `GET /changelog` — `Manager.ChangeLog` newest-first, `limit≤500`.
- `GET /latencia` — One-shot benchmark across 15 collections. Returns `{total_ms, total_docs, queries, resultados[]}` sorted by slowest.

#### 7.10.6 Asistente observability

- `GET /asistente/stats?horas=` — Counts (`ok|error|truncated`), token usage, avg/p95 latency, estimated cost (Gemini Flash pricing constants, kept for cost parity with prior provider).
- `GET /asistente/logs?horas=&estado=&limit=` — Newest-first entries from `Manager.AsistenteLogs`.
- `GET /asistente/timeseries?horas=` — Hourly buckets with `count`, `tokens`, `errors`.
- `GET /asistente/tools-ranking?horas=` — Tools ordered by invocation count with `ok`/`fail`.

#### 7.10.7 Intel ingest

Reports are ingested as PDF or pasted text, the Gemini JSON-mode extractor parses 12 macro variables, and the last confirmed `IntelDoc` is injected into the assistant context automatically.

| Method | Path | Description |
|---|---|---|
| POST | `/intel/extract` | `multipart/form-data` — `fuente` (required), optional `fecha`, `titulo`, `texto`, `pdf` (≤10 MB). Returns preview `{fuente, fecha, titulo, raw_text, extracted, chars}` — **does not persist** |
| POST | `/intel/save` | JSON body `{fuente, fecha, titulo, raw_text, extracted}`; writes `Manager.IntelDocs` with `confirmed=true` |
| GET | `/intel` | List newest-first (`limit≤200`, `fuente`) |
| GET | `/intel/latest` | Last confirmed report (used by `api/agent/context.py`) |
| GET | `/intel/{id}` | Fetch by Mongo `_id` |
| PATCH | `/intel/{id}` | Partial update: `fuente`, `fecha`, `titulo`, `extracted` |
| DELETE | `/intel/{id}` | Remove |

#### 7.10.8 Resources

| Method | Path | Description |
|---|---|---|
| GET | `/resources` | Current snapshot: system (CPU%, load avg, memory, swap, disk, uptime) + per-process (`api`, `motor_*`, `cloudflared`) |
| GET | `/resources/history?limit=60` | Up to 180 snapshots from the in-memory sampler (1 sample/min, 3 h window) |

The sampler is an `asyncio.Task` started in the FastAPI `lifespan`; it survives across requests and stops cleanly on shutdown.

---

### 7.11 Chat (`/api/chat`) · admin

| Method | Path | Limit | Description |
|---|---|---|---|
| POST | `/api/chat` | 30/min, 500/day | Trading-desk assistant turn |

**Request**

```json
{
  "message": "¿Cuál es la TEA de TZX26?",
  "history": [ { "role": "...", "content": [...] } ]
}
```

`message`: 1–4000 chars. `history`: optional, in the canonical Claude-style shape used by the runner (`api/agent/runner.py`).

**Response**

```json
{
  "reply":     "…",
  "tool_calls": [ { "name": "obtener_serie_macro", "args": {...}, "ok": true } ],
  "history":    [ ... ],
  "usage":      { "promptTokenCount": 1234, "candidatesTokenCount": 567, "totalTokenCount": 1801 },
  "steps":      2,
  "elapsed_s":  4.27,
  "truncated":  false,
  "model_used": "claude-haiku-4-5-20251001"
}
```

`model_used` reports the model chosen by `decide_model()` (Haiku vs Sonnet) for Claude runs. Errors follow the typed shape (§5).

Every turn — success, truncation or error — is persisted to `Manager.AsistenteLogs` and surfaced in `/api/manager/asistente/*`.

---

## 8. Data architecture

Source collections are the system of record. API-facing collections are denormalised copies refreshed by scheduled jobs. Cotizaciones and Manager read live.

| API DB / Collection | Source | Sync |
|---|---|---|
| `CuentasAPI.AccionistasAPI` | `CashFlow.Accionistas` | `scripts.api_migrate accionistas` |
| `CuentasAPI.ContrapartesAPI` | `CashFlow.Contrapartes` | `scripts.api_migrate contrapartes` |
| `OperacionesAPI.MesaAPI` | `CashFlow.Flujo` | `scripts.api_migrate flujo` |
| `OperacionesAPI.FlujosAPI` | `CashFlow.Movimientos` | `scripts.api_migrate movimientos` |
| `PortfolioAPI.CarterasAPI` | `Valuaciones.Carteras` | `scripts.api_migrate carteras` |
| `PortfolioAPI.AumAPI` | `Valuaciones.AuM` | `scripts.api_migrate aum` |
| `TitulosAPI.AssetsAPI` | `Valuaciones.Assets` | `scripts.api_migrate assets` |
| `TitulosAPI.ValuacionesAPI` | `Trading.Curvas` + `Trading.BondsMaster` | `scripts.api_migrate flujos-titulos` |
| `Trading.*` (live / hist) | — | Motors write real-time |
| `Opciones.*` | — | Motor `engines.options` + `jobs.options_rollup` |
| `Valuaciones.Dolar` | — | `engines.dolar_mep` |
| `News.Headlines` | — | `jobs.news_ingesta`, `jobs.news_finnhub` |
| `Market.Quotes` / `Market.EconomicCalendar` | — | `jobs.market_quotes`, `jobs.market_anchors`, `jobs.economic_calendar` |
| `Manager.JobRuns` | — | Background writers + TTL 60 d |
| `Manager.AsistenteLogs` | — | Written by `POST /api/chat` |
| `Manager.IntelDocs` | — | Written by `/api/manager/intel/*` |

**Automated re-sync** (`jobs/sync_api_copies.py`) is chained in the crontab after each source job so API copies stay fresh without human intervention. See `deploy/crontab.txt`.

---

## 9. Project structure

```
api/
├── main.py               # FastAPI app, lifespan, middlewares, router dependency wiring
├── auth.py               # Cloudflare Access JWT validation, require_manager dependency
├── ratelimit.py          # SlowAPI Limiter, per-identity key_func
├── cache.py              # @cached(ttl=N) decorator (in-process, no negative caching)
├── db.py                 # get_db_* helpers (no FastAPI import → reusable from services)
├── deps.py               # verify_api_key + re-export of db helpers (compat)
├── services/             # pure-Python service layer (used by routers AND agent dispatch)
│   ├── cotizaciones.py   # listar_curva, market snapshot, forwards, breakevens, historic
│   └── macro.py          # obtener_serie_macro, clasificar_nivel
├── agent/                # LLM assistant (docs/ASISTENTE.md is authoritative)
└── routers/
    ├── analitica.py          # /api/analitica/*
    ├── carteras.py           # /api/portfolio/*
    ├── chat.py               # /api/chat               (admin)
    ├── cotizaciones.py       # /api/cotizaciones/*
    ├── cuentas.py            # /api/cuentas/*
    ├── manager.py            # /api/manager/*          (admin)
    ├── manager_resources.py  # /api/manager/resources* (admin)
    ├── market.py             # /api/market/*
    ├── news.py               # /api/news*
    ├── operaciones.py        # /api/operaciones/*
    └── titulos.py            # /api/titulos/*
```

---

## 10. Deployment

Systemd unit `api.service` on the DigitalOcean droplet (path `/root/TradingAV`, venv `/root/TradingAV/venv/bin/python`). Uvicorn binds to `127.0.0.1:8000`; Cloudflare Tunnel (`cloudflared.service`) publishes it as `api.acaquant.com`.

At startup the `lifespan` hook pings both Mongo clients (pool warmup) and starts the resource sampler. Shutdown cancels the sampler cleanly.

```bash
# Reload after deploy
sudo systemctl restart api.service
journalctl -u api.service -f
```

`MongoClient` instances are shared singletons with `serverSelectionTimeoutMS=30 000` and `compressors="zstd,snappy,zlib"`. Never call `.close()` on them.

---

## 11. Testing

```bash
pytest -ra                              # unit tests (no Mongo)
pytest tests/unit/test_black_scholes.py # single file
pytest -m integration                   # requires Mongo — excluded by default

python -m scripts.test_api              # smoke tests against localhost:8000
python -m scripts.test_api http://host:8000
python -m scripts.perf_scan             # static analysis for Mongo anti-patterns
```

---

## 12. Changelog

| Date | Change |
|---|---|
| 2026-04-15 | Initial release: `/api/health`, `/api/cuentas/*`, `/api/operaciones/*` |
| 2026-04-16 | Add `/api/portfolio/*` (renamed DB to `PortfolioAPI`) and `/api/titulos/*` |
| 2026-04-16 | Add six `/api/cotizaciones/*` endpoints reading live from `Trading.*` |
| 2026-04-16 | Add `/api/cotizaciones/opciones` (Greeks), rename `ticker`/`symbol` → `instrumento` |
| 2026-04-16 | Add `/api/cotizaciones/historico/*` — forwards, breakevens, trades (last 15 d) |
| 2026-04-16 | Add `/api/cotizaciones/mep` and `/api/cotizaciones/historico/mep` |
| 2026-04-16 | Automate API-copy re-sync via `jobs.sync_api_copies` chained in crontab |
| 2026-04-19 | Add `/api/chat` (Claude + tool-use, Gemini fallback); observability under `/api/manager/asistente/*` |
| 2026-04-19 | Add `/api/manager/intel/*` for research-report ingest with JSON-mode extraction |
| 2026-04-19 | Add `/api/manager/resources*` + background sampler in FastAPI `lifespan` |
| 2026-04-20 | Tier 1 security: SSRF validation in `/api/news/article`, Cloudflare JWT validation, `require_manager`, per-identity rate limiting |
| 2026-04-20 | Tier 2 refactor: extract service layer (`api/services/*`), remove HTTP loopback agent→API, add `@cached` at service level |
| 2026-04-20 | Add `/api/analitica/*` (Tier 1 assistant tools exposed over HTTP) |
| 2026-04-20 | `api/auth.py` accepts service-token JWTs from acaquant-web SSR; `CF_TRUSTED_SERVICE_TOKENS` allow-list |
| 2026-04-21 | Add typed error model (`{detail:{code,message,retryable,retry_after_s?}}`) for `/api/chat` and rate limiting |
