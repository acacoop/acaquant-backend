# TradingAV API

**Version:** 0.3.0
**Base URL (prod):** `https://api.acaquant.com`
**Base URL (dev):** `http://127.0.0.1:8000`
**Protocol:** REST over HTTP/1.1 · JSON
**Repo:** [`api/`](../api/)

---

## 1. Overview

The TradingAV API is a FastAPI service that exposes the quantitative data and trading layer of TradingAV — microstructure for fixed income and options on Argentine markets (MERVAL/ROFEX), portfolio analytics, MEP execution, account risk, news / macro context and assistant tooling.

It is the only consumption boundary of the platform: the production frontend (**acaquant-web**, Next.js on Vercel) proxies every read and write through it, the trading-desk assistant (`/api/chat`) dispatches its tool calls against the same service layer, and a read-only **MCP server** (`/mcp`) re-exposes a subset of the analytical surface to Claude Desktop / claude.ai via Custom Connectors. See `docs/MCP.md`.

**Design principles**

- **Read-only by default, mutations are explicit.** Every `GET` reads through the SQL read pool (`core.postgres.get_pool()`). The handful of mutating endpoints (`POST/PUT/PATCH/DELETE`) is enumerated and gated.
- **Thin routers, fat services.** Router modules under `api/routers/` parse params and delegate to pure-Python services under `api/services/`. The same services are invoked by the assistant tool registry without HTTP loopback and by the MCP server tools.
- **Defense in depth.** Cloudflare Tunnel + Access (SSO + service tokens), signed JWT validation, bearer key, rate limiting by identity, **role-based access control (RBAC) per module** with audit log, and explicit SSRF protection on user-supplied URLs.
- **SQL-native reads.** Heavy analytical endpoints read directly from the Postgres/Supabase tables (e.g. `portafolio.tenencia`, `portafolio.assets`, `operaciones.operaciones`); the pre-aggregated `*API` mirror collections were eliminated and the API now joins sources at read time (`api/services/titulos_flujos.py`).

---

## 2. Quick start

```bash
# Local dev
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000

# Interactive exploration
open http://localhost:8000/docs            # Swagger UI
open http://localhost:8000/redoc           # ReDoc
```

Health probe (always unauthenticated):

```bash
curl https://api.acaquant.com/api/health
# → {"status":"ok"}
```

---

## 3. Authentication & authorization

The API runs behind four independent layers. A request must pass all four to reach a handler that requires a restricted module.

```
        ┌──────────────────┐
client →│ Cloudflare Tunnel│→ origin
        └──────────────────┘
                 │
                 ▼
        ┌──────────────────┐
        │ Cloudflare Access│ (identity: user JWT or service token)
        └──────────────────┘
                 │
                 ▼
        ┌──────────────────┐
        │ Bearer API key   │ (verify_api_key)
        └──────────────────┘
                 │
                 ▼
        ┌──────────────────┐
        │ require_module(m)│ (RBAC, core/roles_sql.py)
        └──────────────────┘
                 │
                 ▼
              handler
```

### 3.1 Cloudflare Tunnel

The origin server has no public IP. All inbound traffic terminates at Cloudflare and is tunneled to the Droplet over `cloudflared.service`. Direct internet exposure is impossible.

### 3.2 Cloudflare Access (identity)

`api.acaquant.com` sits behind a Zero Trust application. Two credential types are accepted:

1. **User SSO** — e-mail OTP login. CF injects:
   - `Cf-Access-Authenticated-User-Email`
   - `Cf-Access-Jwt-Assertion` (RS256, validated by the service against `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs` with audience `CF_ACCESS_AUD`).
2. **Service token** — used by the Next.js SSR layer of acaquant-web. The frontend includes:
   - `CF-Access-Client-Id`
   - `CF-Access-Client-Secret`

   CF validates the secret and emits a JWT with `common_name` (no `email` claim). Only `common_name`s listed in `CF_TRUSTED_SERVICE_TOKENS` (env, comma-separated) are accepted.

> **Important — service token quirk.** When a request enters the origin authenticated by service token, Cloudflare **strips `cf-access-authenticated-user-email`** that the origin tried to forward. CF only emits that header itself when validating a user JWT. This forced the introduction of `X-Acaquant-User-Email` (§3.4) — without it the backend has no way to identify the human user behind a Vercel SSR request.

If `CF_ACCESS_TEAM` / `CF_ACCESS_AUD` are empty (development), JWT validation is skipped and the service falls back to the raw email header.

### 3.3 Bearer API key

Every endpoint except `/api/health` requires:

```http
Authorization: Bearer <API_KEY>
```

`API_KEY` is a static secret shared between server (`.env`) and clients (acaquant-web `.env.local`, internal scripts). If `API_KEY` is empty, bearer check is disabled (dev only).

### 3.4 RBAC per module

Identity tells us **who is calling**; RBAC tells us **what they can see**. Backed by three SQL tables (`core/roles_sql.py`):

| Table | Purpose |
|---|---|
| `manager.users` | `{email, role, enabled, created_at, updated_at, auto_registered?, notes?}`. Auto-registers the first time a new email is seen (default role = `sales`). |
| `manager.role_matrix` | `{role, modules: [str], updated_by, updated_at}`. Editable from the Manager panel. |
| `manager.role_audit` | Append-only `{ts, actor, action, target, before, after}` for every user/matrix mutation. |

**Modules** (canonical list in `core/roles.py::MODULES`):

```
home · renta-fija · derivados · estrategia · operar · operaciones · portfolios · asistente · mm · manager
```

> **`operar` vs `operaciones`** — son módulos distintos. `operar` cubre **acciones del trading desk** (mandar/cancelar órdenes, MEP, riesgo de cuenta) y lo tienen `admin + trader + sales`. `operaciones` cubre la **mesa de flujo** (boletos, cash movements, contrapartes) y lo tienen `admin + trader` solamente — sales NO.

**Default matrix** (`DEFAULT_MATRIX` — used until the admin overrides per role from the panel):

| Role | Modules |
|---|---|
| `admin` | all 10 |
| `trader` | home, renta-fija, derivados, estrategia, **operar**, operaciones, portfolios, asistente |
| `sales` | home, renta-fija, derivados, estrategia, **operar** |

Enforcement:

- `api/main.py` wraps every router with `Depends(require_module("<m>"))` based on the path prefix:
  ```
  /api/portfolio, /api/titulos              → portfolios
  /api/ordenes, /api/operativa, /api/risk   → operar       (sales puede)
  /api/operaciones, /api/cuentas,
  /api/mesa-dinero                          → operaciones  (admin + trader)
  /api/chat                                 → asistente
  /api/mm                                   → mm           (admin only por default)
  /api/manager                              → manager
  ```
- `home / renta-fija / derivados / estrategia` paths use `_PUBLIC` (auth + bearer, no module gate).
- `/api/me` and `/api/simulaciones/*` have no module gate; ownership is enforced inside the service (`user_email` filter).
- `/api/derivados/agro` está bajo `_PUBLIC` para el routing pero el handler aplica un check inline `role == "admin"` (el resto de `/derivados` sigue público para todos los roles con módulo `derivados`).

### 3.5 Identity propagation: `X-Acaquant-User-Email`

The frontend talks to the API server-side via service token. Because CF strips the CF-prefixed email header in that mode (§3.2), acaquant-web propagates the user identity in a custom header:

```http
X-Acaquant-User-Email: jdoe@acavalores.com
```

`api/auth.py::get_user_email` resolves identity in this order:

1. **User JWT** (direct OTP login, rare on the API path) → email claim from JWT.
2. **Service token JWT** + `X-Acaquant-User-Email` → forwarded user email (the standard production path from acaquant-web).
3. **Service token JWT** without `X-Acaquant-User-Email` → synthetic `service:<common_name>` identity. `get_user_role()` falls back to `DEFAULT_ROLE` (`sales`); restricted modules return `403`.

> **Security note.** Until 2026-04-28 the synthetic `service:*` identity was implicitly mapped to `admin`. This was a **privilege-escalation bug** — every user-driven request reached the backend as `service:<cn>` (because of the CF stripping above) and was treated as admin, ignoring `manager.users` entirely. Fixed in commit `12b7008`: `service:*` now resolves to `DEFAULT_ROLE`. Machine integrations that need elevated access must register their email/CN explicitly in `manager.users`.

### 3.6 Backwards compatibility — `require_manager`

`api/auth.py::require_manager` still exists as a thin alias over `require_module("manager")` for legacy callers. New code should use `require_module(m)` directly.

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

**Simple:** `{"detail": "<message>"}` — for `400/401/403/404/500/502` from standard handlers.

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

| Code | HTTP | Meaning |
|---|---|---|
| `rate_limit` | 429 | Per-endpoint quota exceeded or upstream LLM rate limit. Retry after `retry_after_s`. |
| `transport` | 503 | Could not reach LLM provider. |
| `bad_response` | 502 | LLM returned a malformed payload. |
| `llm_error` | 502 | Generic LLM-side error. |
| `internal` | 500 | Unhandled server error. Not retryable. |

---

## 6. Conventions

- **Path prefix.** Every route is under `/api`. Health is `/api/health`. MCP is mounted at `/mcp` (separate auth, see `docs/MCP.md`).
- **Date format.** `YYYY-MM-DD` for calendar days, ISO-8601 with `+00:00` for timestamps. Some live endpoints serialize `mercado.timesales` timestamps as ART-shifted UTC (`+03:00` from naive ART) — see `MOTOR_TS_OFFSET` in `api/services/operativa_mep.py` and `api/services/triggers_mep.py`.
- **Currency.** `ARS` or `USD` in the `moneda`/`unidad` field.
- **Account.** ROFEX accounts are passed as plain strings (`"805"`). The default account comes from `ROFEX_ACCOUNT` (env). The environment used (`live` vs `remarket`) is controlled by `ROFEX_ORDERS_ENV`.
- **Filters.** All query params are optional unless marked required. Missing filters return the full table (subject to projection).
- **Response envelopes.** List endpoints return a JSON array. Analytical endpoints return an object with the minimal shape described per route.
- **Projection.** Responses are projected server-side to remove `_id` and reduce payload. Field lists are authoritative in this document; clients must tolerate new fields.
- **Compression.** Responses ≥ 1 KiB are served with `Content-Encoding: gzip` when the client advertises it (`GZipMiddleware`).
- **Cache.** Hot reads are cached in-process by the service layer (TTL 5 s for live MEP up to 3600 s for BCRA). Account endpoints (`/api/risk/*`, `/api/ordenes/*`) are NOT cached — always fresh from the broker. Mutating endpoints clear or bypass cache.
- **CORS.** Not enabled. The only public consumer is acaquant-web; it calls the API server-side through its proxy routes.

---

## 7. Endpoint reference

Routes are grouped by tag. Access column:

- **pub** — `_PUBLIC` (auth + bearer, no module gate). Visible to every authenticated user including `sales`.
- **port** — `_PORTFOLIOS` (`portfolios` module).
- **opr** — `_OPERAR` (`operar` module — admin + trader + sales: trading-desk actions).
- **op** — `_OPERACIONES` (`operaciones` module — admin + trader: mesa de flujo).
- **chat** — `_ASISTENTE` (`asistente` module).
- **mm** — `_MM` (`mm` module — admin only by default).
- **adm** — `_MANAGER` (`manager` module — admin only by default).
- **own** — no module gate; ownership filter inside the service (`user_email`).

### 7.1 Health & identity

| Method | Path | Access | Description |
|---|---|---|---|
| GET | `/api/health` | — | Liveness probe. No auth. |
| GET | `/api/me` | own | Caller identity: `{email, role, modules, is_admin}` |

`/api/me` is consumed by the frontend layout to filter the nav and by `src/proxy.ts` to redirect users away from modules they don't have. `is_admin` is sugar for `role == "admin"`.

---

### 7.2 Cotizaciones (`/api/cotizaciones/*`) · pub

Live and historical market data. All reads go directly against the SQL market tables (`mercado.*`, `macro.*`, `valuaciones.*`).

| Method | Path | Summary |
|---|---|---|
| GET | `/badlar` | BADLAR series (BCRA id=7) |
| GET | `/cer` | CER series (BCRA id=30) |
| GET | `/dolar` | Official USD (A3500, BCRA id=5) |
| GET | `/mep` | Latest MEP/CCL/canje (live `valuaciones.dolar_snapshot`, falls back to cron histórico) |
| GET | `/forwards` | Live forward-rate matrix |
| GET | `/forwards-zscore` | Z-score of a forward vs N-day rolling window |
| GET | `/fair-value` | Bond fair value live (vs YTM curve) |
| GET | `/fair-value/cierre` | Same, computed at last close |
| GET | `/fair-value/historico` | Per-bond fair-value series |
| GET | `/breakevens` | Live breakeven inflation |
| GET | `/rem/informes` | List of REM (BCRA expectations survey) reports available |
| GET | `/rem` | REM expectations table |
| GET | `/rem/breakeven-acumulado` | Cumulative breakeven implied by REM expectations |
| GET | `/rem/debug` | Diagnostics for the REM aggregation |
| GET | `/renta-fija` | Fixed-income market snapshot |
| GET | `/opciones` | Options chain with Greeks |
| GET | `/opciones/meta` | Risk-free rate + VR anchors |
| PUT | `/opciones/tasa` | Update risk-free rate (`valor` 0<v<3). Clears cache. |
| GET | `/caucion` | Live TNA caución ARS + USD (next business-day tenor) |
| GET | `/futuros-dlr` | Live DLR outright curve with implied TNA |
| GET | `/argy` | MEP/CCL/canje/caución panel with %Día/%7d/%MTD/%YTD |
| GET | `/historico/mep` | MEP series |
| GET | `/historico/dolares` | Multi-USD type history (D, C, MEP, oficial, …) |
| GET | `/historico/forwards` | Forward matrices by date |
| GET | `/historico/breakevens` | Breakevens by date |
| GET | `/historico/trades` | Raw trades (price/size/side) from `mercado.timesales`, last 15 days, hard-limit 10 000. Analytics (TEA/TEM/duration/paridad) populated only for trades **before 2026-05-04** (motor_curvas stopped enriching TimeSales after that). |
| GET | `/historico/opciones` | Option trades, last 21 days, hard-limit 5 000 |
| GET | `/historico/curva` | Daily close per ticker in a curve (`curva` required). Source: `mercado.snapshots_cierre` (cron 20:25 UTC L-V). If today has no persisted close yet, includes a live row per ticker from `mercado.market_snapshot.metrics`. |
| GET | `/historico/caucion` | Daily TNA close by currency |
| GET | `/historico/futuros-dlr` | Daily DLR futures close |

#### Common query parameters

| Param | Type | Notes |
|---|---|---|
| `desde` / `hasta` | `date` | `YYYY-MM-DD` inclusive |
| `instrumento` | `string` | Short ticker (`TX26`) or full ROFEX (`MERV - XMEV - TX26 - 24hs`). Short tickers are resolved against `mercado.curvas.ticker_corto`. |
| `curva` | `enum` | `tasa_fija` \| `cer` \| `tamar` \| `soberanos` \| `dolar_linked` |
| `tipo` | `enum` | `CALL` \| `PUT` (options) |

#### Bond analytics fields (TEA / duration / paridad / etc)

These fields are computed by `engines/curvas.py` (refresh ~2s) and persisted in two places:

- **Live** — `mercado.market_snapshot.metrics`. Surfaced by `/listar-curva`, `/snapshot-curva-historico` (when `fecha=today` before the close cron), and the live row of `/historico/curva` for today.
- **Historical** — `mercado.snapshots_cierre` (one row per `(curva, ts_cierre, ticker)`, populated by `jobs/snapshot_cierre.py` at 20:25 UTC and the `backfill_snapshots_cierre` script). Surfaced by `/historico/curva`, `/snapshot-curva-historico` (past dates), `/serie-macro` with `<TICKER>.<FIELD>`, etc.

Field semantics:

| Field | Instruments | Meaning |
|---|---|---|
| `duration` | all | Macaulay duration (years) |
| `convexity` | all | Bond convexity |
| `TEA` | `tasa_fija` + `cer` | Annual effective rate |
| `TEM` | `tasa_fija` | Monthly effective rate |
| `paridad` | `cer` + soberanos | `price / (VN × CER_trade / CER_emision) × 100` |

**Note on `/historico/trades`:** before 2026-05-04 these fields were also stamped per-trade in `mercado.timesales` (motor_curvas wrote them on every tick). After that refactor timesales is append-only price/size/side; the analytics live exclusively in `mercado.market_snapshot` (live) and `mercado.snapshots_cierre` (close). For "TEA history of bond X" prefer `/historico/curva` filtering by ticker on the client, or `/serie-macro?variable=<TICKER>.TEA`.

---

### 7.3 Analítica (`/api/analitica/*`) · pub

HTTP projection of the assistant tools. Same functions invoked by the agent's tool registry without HTTP loopback.

| Method | Path | Summary |
|---|---|---|
| GET | `/listar-curva` | Enriched bond table per curve |
| GET | `/serie-macro` | Series for a macro variable or `<TICKER>.<FIELD>` |
| GET | `/clasificar-nivel` | Percentile classification vs window |
| GET | `/snapshot-curva-historico` | Curve at a past date |
| GET | `/pendiente-curva` | Slope (long − short) in bps; optional vs past date |
| GET | `/liquidez-secundario` | Today's volume vs N-day average + classification |
| GET | `/sensibilidad-retorno` | Bond return sensitivity to YTM/duration shifts |
| GET | `/canje` | AL30/AL30D canje analysis (long ARS / short USD) |
| GET | `/carry-trade` | Local carry vs forward-implied devaluation |
| GET | `/descomposicion-retorno` | Ex-post return decomposition (carry / Δprecio / FX) |
| GET | `/rolldown-esperado` | Expected roll-down on the curve |
| POST | `/estrategia-historico` | Simulate a strategy over history (`POST` for body-shape inputs) |

#### `GET /listar-curva`

| Param | Type | Required | Notes |
|---|---|---|---|
| `curva` | enum | yes | `cer` \| `tasa_fija` \| `tamar` \| `soberanos` \| `dolar_linked` |
| `ordenar_por` | enum | no | `vencimiento` (default) \| `volumen_dia` \| `tea` \| `duration` |
| `vencimiento_min_meses` | float | no | Horizon lower bound (months) |
| `vencimiento_max_meses` | float | no | Horizon upper bound (months) |
| `limit` | int | no | Top-N after sort |

Each element: `ticker, ticker_corto, tipo, fecha_vencimiento, fecha_emision, meses_al_vto, ultimo_precio, tea, tem, paridad, duration, convexity, total_money_dia, total_nominals_dia, ts_ultimo_trade`. CER and dollar-linked rows additionally include `tc_breakeven` (`MEP × flujo_vencimiento / precio_actual`, computed live, never persisted).

#### `GET /serie-macro`

`variable` accepts keyword aliases (`tamar, cer, dolar, badlar, mep, ccl, canje, caucion_ars, caucion_usd, ipc, ipim, riesgo_pais, repo, rem_inflacion`) or a `<TICKER>.<FIELD>` reference. `ventana_dias` bounded to `[1, 3650]` (default 90).

#### `GET /pendiente-curva`

Slope of a curve (longest-duration bond minus shortest), in basis points. Optional `fecha_comparacion` adds past slope and `delta_bps` with qualitative interpretation.

#### `GET /liquidez-secundario`

Day-over-average volume ratio for a specific bond. Classification: `baja` (<0.3), `media` (0.3–1.5), `alta` (1.5–3.0), `anomalamente_alta` (>3.0), `sin_datos`.

#### `GET /canje` — AL30 vs AL30D

Cross-MEP arb monitor. Joins live AL30 (CI/24hs) with AL30D, computes implied MEP, and compares against the official MEP. Returns the spread series + classification.

#### `GET /carry-trade`

Implied local carry vs forward-implied devaluation (ROFEX DLR). Daily prices source: `mercado.snapshots_cierre` (cron 20:25 UTC L-V). If the requested range includes today and the close cron has not run yet, a live point is appended from `mercado.market_snapshot.metrics.last_price` so the series always reaches "today" during market hours. MEP comes from `valuaciones.dolar` (written by `engines/dolar_mep` every 15 min L-V 13–20 UTC).

#### `GET /descomposicion-retorno`

Ex-post return decomposition between two dates. Modes: `realizado` (ex-post, requires both dates) and `proyectado` (live carry). CER curve supported. Both endpoints read `mercado.snapshots_cierre` via `snapshot_curva_historico`; if either bound is today and no close is persisted, the same live fallback to `mercado.market_snapshot.metrics` applies. See `api/services/descomposicion_retorno.py` for the full formula breakdown.

#### `POST /estrategia-historico`

Backtests a strategy with body-passed inputs (positions + dates + initial capital). Body schema in `api/services/estrategia_historico.py`.

---

### 7.4 Cuentas (`/api/cuentas/*`) · op

Counterparty / shareholder reference data (lives in SQL — counterparties in `clientes.contrapartes`).

| Method | Path | Summary |
|---|---|---|
| GET | `/accionistas` | Shareholder accounts |
| GET | `/contrapartes` | Counterparty accounts (`grupo ∈ {Fondos, ALYC, Bancos}`) |

Shared schema: `cuenta`, `id_cuenta`, `nombre`, `grupo`.

---

### 7.5 Operaciones (`/api/operaciones/*`) · op

Mesa flow + cash movements (read-only). **Bloqueado al asistente y al MCP por policy** (datos privados de la mesa).

| Method | Path | Summary |
|---|---|---|
| GET | `/flujo` | Trade flow (per-boleto) from `operaciones.operaciones` |
| GET | `/flujos` | Cash movements from `operaciones.operaciones` |
| GET | `/fondos` | Counterparties `grupo=Fondos` with at least one FCI asset |
| GET | `/negocio` | Negocio del día consolidado por boleto desde `operaciones.negocio_movimientos` (param `fecha=YYYY-MM-DD`, default hoy ART). Devuelve `meta` + `agregados` por categoría + `top_tickers` + `boletos`. |
| GET | `/negocio/fechas` | Lista de fechas con boletos persistidos en `operaciones.negocio_movimientos` ordenadas desc. Devuelve `[{fecha, n}]`. Usado por el frontend para limitar el selector. |
| GET | `/ops/resumen` | Vista MOVIMIENTOS: `por_operacion` / `por_denominacion` / `por_instrumento` (cross-filter 3-way) + `total`. Cada fila trae `bruto`, `arancel`, `n` y **`tasa_pond`**. |
| GET | `/financiamiento` | Tab FINANCIAMIENTO: libro VIVO de pagarés/cheques (assets con `cartera='FINANCIAMIENTO'` y vencimiento ≥ hoy) al grano cuenta × instrumento. Ver abajo. |

**`/financiamiento` (2026-08-11)** — devuelve `{fecha, hoy, n, con_tasa, truncado, filas[]}`,
una fila por (cuenta, instrumento) con `id_cuenta`, `cuenta`, `unidad`, `ticker`,
`emisor`, `clase`, `vencimiento`, `dias`, `cantidad`, `moneda`, `tasa`, `tasa_min`,
`tasa_max`, `n_boletos`. Manda el GRANO y no agregados a propósito: las cuatro
tablas de la pantalla se cruzan entre sí y resolverlo server-side costaría un
round-trip por click.

- **`clase` = `HD` | `DL` | `''`** (de `assets.clase_activo`). Son ESCALAS, no
  etiquetas: la vista muestra una por vez y **nunca las suma**. `''` = el job
  todavía no clasificó ese asset; se devuelve igual y la vista lo agrupa aparte
  en vez de esconder posiciones reales. La infiere
  `jobs/assets_autofill.py` (regla `financiamiento_clase`, ≤ 5.000.000 → HD) y se
  corrige a mano en Manager → ASSETS, que el job nunca pisa.
- `moneda` viene de la tenencia y es **informativa** — sirve para ver si coincide
  con la clase inferida, pero no es lo que separa las escalas.

- **No hay ningún campo de plata, a propósito.** Estos papeles se compran con
  descuento y la mayoría son dólar-linked liquidados en pesos → el bruto no
  compara entre filas. Lo que importa es el NOMINAL (`cantidad`) y la `tasa`.
- **Dos fuentes.** `cantidad` sale de `portafolio.tenencia` del último snapshot
  (la POSICIÓN: sumar boletos daría el flujo y contaría dos veces lo que entró y
  salió). `tasa` sale de `operaciones.operaciones.tasa` de los boletos MAV, que
  rellena `jobs/ops_tasa_mav.py`.
- **El puente entre ambas es (`id_cuenta`, código del instrumento)**: en assets el
  código es el `ticker` (contenido del corchete de la unidad) y en el movimiento
  es el mismo corchete dentro de `negocio_movimientos.informacion`. Se matchea por
  cuenta Y código — la tasa es de la OPERACIÓN de ese cliente, no del papel.
- **`tasa: null` = sin dato, se muestra vacío** (nunca `0`, que sería una tasa
  real). Una posición sin boleto MAV con tasa parseada es normal. `con_tasa` dice
  cuántas filas resolvieron; `scripts/diag_financiamiento.py` mide la cobertura.
- Con varios boletos del mismo par, `tasa` es el promedio **ponderado por
  nominal**; `tasa_min`/`tasa_max` viajan para que la vista marque cuándo ese
  promedio esconde dispersión.
- `@cached(300)`, scopeado por grupos (`scope_cuentas`), tope de 20.000 filas
  (lo avisa en `truncado` en vez de servir una tabla incompleta en silencio).

**`tasa_pond` (2026-08-03)** — tasa PONDERADA POR VOLUMEN del grupo, en porcentaje
(`6` = 6%, admite negativas). Se calcula server-side (`Σ(tasa·bruto)/Σ(bruto)`,
misma dolarización que `bruto`) justamente para que el frontend NO tenga que
ponderar: se pinta tal cual viene.

- **`null` = sin dato, y hay que mostrarlo vacío** (no `0`, que sería una tasa
  real). Es lo normal fuera de MAV: `operaciones.tasa` sólo está poblada para
  los boletos MAV (pagarés/cheques, que se negocian a tasa y no a precio
  unitario — la rellena `jobs/ops_tasa_mav.py`).
- Las filas sin tasa se excluyen del promedio (no diluyen hacia cero): un grupo
  mixto pondera sólo sobre el volumen que efectivamente tiene tasa.
- En `por_instrumento` (POR TÍTULO) los boletos de un mismo título comparten
  tasa, así que la ponderada colapsa a ese único valor.

**`moneda=USD_DOL` (DOLARIZAR) — fallback de TC (2026-08-10)** — el volumen
dolarizado convierte cada boleto ARS con SU snapshot `operaciones.mep`. Los
boletos que escribe `jobs/fci_bilateral` nunca estamparon `mep` y la expresión
vieja los mandaba a **cero**: FCI Bilateral mostraba volumen ARS real y ~0 al
dolarizar (julio 2026). Ahora `operaciones_sql._MEP_ROW` cae al **MEP del día de
concertación** (`valuaciones.dolar`, mismo criterio que `dolar_sql.mep_para_fecha`)
cuando el boleto no trae el suyo — la subquery vive dentro de una rama del `CASE`,
así que sólo se evalúa en las filas sin snapshot. Aplica igual al `arancel` en
USD/USD_DOL. Sólo queda sin convertir lo anterior al inicio del feed MEP (`NULL`,
que `SUM` ignora — no se cuenta como cero).

Complementos: `jobs/fci_bilateral` ya estampa `mep` al insertar (y lo RELLENA en
`ON CONFLICT`, nunca lo pisa) y `scripts/backfill_ops_mep.py` materializa el
histórico. Tras el backfill hay que recomputar el agregado frío
(`python -m jobs.ops_agregado --full`), que usa las MISMAS expresiones.

#### `/negocio` — schema del response

`agregados[]` (uno por categoría presente en el día):

```json
{ "categoria": "compra", "n": 45, "importe_neto": 1200000.0, "importe_abs": 1200000.0, "n_cuentas": 12, "n_tickers": 8, "monedas": ["ARS"] }
```

`boletos[]` (un objeto por comprobante consolidado):

```json
{ "fecha": "2026-05-04", "comprobante": "BOL 2026069919", "cuenta": "[805] MOLLO ...",
  "categoria": "compra", "op": "Compra", "ticker": "AL30",
  "cantidad": -1.00, "precio": 91410.0, "importe": 91410.0,
  "moneda": "ARS", "plazo": "Inm", "lugar": "Local", "estado": "DIS",
  "informacion": "Compra [AL30] 1,00@91410,00 (ARS Inm)", "n_lineas": 2 }
```

Categorías posibles (16):

`compra`, `venta`, `suscripcion_fci`, `rescate_fci`, `solicitud_suscripcion_fci`, `solicitud_rescate_fci`, `acreencia`, `caucion_col_ap`, `caucion_col_ci`, `caucion_tom_ap`, `caucion_tom_ci`, `caucion_otro`, `deposito`, `extraccion`, `transferencia`, `comision`, `impuesto`, `otro`.

`importe` y `cantidad` están **siempre en perspectiva del cliente** (positivo = ingresa, negativo = egresa). Aunesa devuelve perspectiva broker; el service invierte el signo.

La data la pobla `jobs/negocio_movimientos.py` cada hora 12-22 ART L-V (escritura SQL-native en `operaciones.negocio_movimientos`). Detalle del flujo en `docs/sesion_2026_05_05_negocio.md`.

---

### 7.5b Mesa de Dinero (`/api/mesa-dinero/*`) · op

Carga manual de operaciones de la mesa (cada registro = pata compra + pata venta). Lectura con módulo `operaciones`; **escritura solo para emails en `operaciones.mesa_dinero_escritores`** (admin siempre puede) — allowlist gestionada desde Manager → MESA. `monto = vn × px / 100`, `resultado = monto_venta − monto_compra` y `pct = resultado / monto_compra` se derivan server-side (`resultado` manual solo si faltan patas, ej. "Pase OPS"). Todo cambio queda auditado en `operaciones.mesa_dinero_audit`.

| Method | Path | Summary |
|---|---|---|
| GET | `/ops` | Operaciones del rango `desde`/`hasta` (YYYY-MM-DD) |
| GET | `/resumen` | Resultado diario: `resultado_ars`, `tc` (manual), `resultado_usd`, acumulados y totales |
| GET | `/opciones` | Catálogos para el form: traders, observaciones (`Mesa` + operadores de `clientes.operadores`) y `puede_escribir` del actor |
| POST | `/ops` | Alta (write-gated) |
| PATCH | `/ops/{id}` | Edición (write-gated) |
| DELETE | `/ops/{id}` | Baja (write-gated) |
| PUT | `/tc` | Upsert del TC del día `{fecha, tc}` (write-gated) |

Gestión admin-only bajo `/api/manager/mesa/*`: `GET/POST/DELETE /traders`, `GET /escritores`, `GET /escritores/candidatos?q=`, `POST/DELETE /escritores`.

---

### 7.6 Órdenes (`/api/ordenes/*`) · opr

Direct ROFEX order send / cancel / status. Backed by `api/services/ordenes.py` over `pyRofex`.

| Method | Path | Summary |
|---|---|---|
| POST | `` | Send a LIMIT or MARKET order. Body: `{ticker, side, size, order_type, price?, tif, account?}`. Returns `{ok, cl_ord_id, status, broker_response, error?}`. `201 Created` on success. |
| DELETE | `/{cl_ord_id}` | Cancel by client order id. Returns `{ok, broker_response, error?}`. The actual ER lands in SQL via the order-reports motor. |
| GET | `/dia` | Orders sent today (UTC) for the default account. |
| GET | `/{cl_ord_id}` | Current order status, maintained by the order-reports motor (`operaciones.ordenes_live`). `404` if unknown. |

`actor_email` is captured from the authenticated identity and persisted in `operaciones.ordenes_audit`. `pyRofex` is initialized lazily on first call (`ensure_session_envio()`); the call is idempotent and thread-safe.

> **Bug fix 2026-04-28** — `send_order` previously called `ensure_session_envio()` only when `account` was `None`. Callers that passed an explicit account (e.g. the trigger scanner) hit pyRofex with no default environment and got `Environment not specify.` Now `ensure_session_envio()` runs unconditionally; account resolution uses `cuenta_default()` separately.

---

### 7.7 Operativa MEP (`/api/operativa/*`) · opr

Wrapped operativa: BUY AL30 + SELL AL30D in one call. Persists to `operaciones.operativas_mep`; the legs live in `operaciones.ordenes_live` and join at read time.

#### Live cotización + chart series

| Method | Path | Summary |
|---|---|---|
| GET | `/mep/cotizacion?rueda=` | Live AL30 / AL30D / implicit MEP for a `rueda ∈ {CI, 24hs}`. |
| GET | `/mep/timesales?rueda=` | Per-minute MEP series, last 24 h. Per-minute bucketing (`date_trunc` + last) server-side. `ts` is in real UTC (offset corrected from the motor's naive ART). |

#### Compra inmediata

| Method | Path | Summary |
|---|---|---|
| POST | `/mep` | Body `{monto_ars>0, comision_pct ∈ [0,5], rueda, account?}`. BUYs AL30 MARKET, waits for ER, then SELLs AL30D MARKET. If BUY rejects/expires, **never** sends SELL (avoids unintended shorts). |
| GET | `/mep/dia?account=` | Operativas of today (UTC), enriched with order legs and computed `usd_efectivo` / `mep_efectivo`. |

`POST /mep` response shape:

```json
{
  "ok": true,
  "operativa_id": "uuid",
  "status": "OK | OK_PARCIAL | FAIL | FAIL_VALIDACION | STALE_BUY",
  "stage": "validacion | buy_ack | buy_er | sell | all_ok",
  "buy":  { "cl_ord_id": "...", "status": "NEW|FILLED|REJECTED|...", "ok": true, "reason": null },
  "sell": { "cl_ord_id": "...", "status": "PENDING_NEW|...", "ok": true, "error": null },
  "nominales": 42,
  "error": null
}
```

`GET /mep/dia` returns `usd_efectivo = sell.cum_qty × sell.avg_px × PRICE_FACTOR_BONOS` and `mep_efectivo = monto_ars / usd_efectivo` — i.e. the **realized** MEP including slippage and BYMA's "price per 100 VN" scaling factor.

#### Triggers (operativa condicional)

Background scanner in `api.main` lifespan polls `operaciones.triggers_mep` every 1 s. Stale guard: if AL30 / AL30D `last_trade > 5 s` old, the trigger does not fire — the cotization may be ghost (motor down). EOD auto-cancel at 19:50 UTC (16:50 ART).

| Method | Path | Summary |
|---|---|---|
| POST | `/mep/trigger` | Body `{monto_ars, comision_pct, rueda, tc_objetivo, tp_objetivo?, sl_objetivo?, account?}`. Creates an `ACTIVE` trigger. With `tp_objetivo` or `sl_objetivo`, it becomes a bracket: after entry, transitions to `WAITING_EXIT` and fires the closing operativa when MEP crosses TP (≥) or SL (≤). |
| DELETE | `/mep/trigger/{trigger_id}` | Cancels a trigger in `ACTIVE` or `WAITING_EXIT`. No-op for any other state. |
| GET | `/mep/triggers/dia?account=` | All triggers created today (UTC), all states. |

Trigger states: `ACTIVE | FIRING | EXECUTED | WAITING_EXIT | EXITING | EXITED | EXIT_FAIL | CANCELLED | CANCELLED_EOD | FAIL`.

---

### 7.8 Risk · cuenta del broker (`/api/risk/*`) · opr

Wrapper over `pyRofex.get_account_*`. Uncached — always fresh from the broker.

| Method | Path | Summary |
|---|---|---|
| GET | `/account/saldo?rueda=&account=` | Per-currency balances (`ARS`, `USD D`, etc.) + per-day movements for a settle (CI=0, 24hs=2). Mirrors the "Posiciones" view in Primary. |
| GET | `/account/report?account=` | Raw `get_account_report` payload (margins, collateral, portfolio, all currencies). |
| GET | `/account/positions?account=` | Aggregated positions (`buySize`, `buyPrice`, `sellSize`, `sellPrice`) per ticker. |
| GET | `/account/detailed?account=` | Detailed positions by instrument type (BOND / NEGOTIABLE_OBLIGATION / …) with market valuation. |

`/account/saldo` response shape:

```json
{
  "account": "805",
  "rueda": "CI",
  "settlement_type": "0",
  "settlement_date": "2026-04-28T03:00:00+00:00",
  "last_calc": "2026-04-28T18:12:51.742+00:00",
  "saldo_ars": 9960264.51,
  "saldo_usd_d": -5183.79,
  "movimiento_ars": 38211.60,
  "movimiento_usd_d": -26.43,
  "monedas": {
    "ARS":   { "available": 9960264.51, "consumed": 38211.60 },
    "USD D": { "available": -5183.79,  "consumed": -26.43 },
    "USD C": { "available": 0.58,       "consumed": 0.0 },
    "...":   { "...": 0.0, "...": 0.0 }
  }
}
```

> **Bug fix 2026-04-28** — `/account/saldo` previously read from `availableToOperate.cash.detailedCash` (post-margin, "what you can operate now"). Primary's "Efectivo Disponible" comes from `currencyBalance.detailedCurrencyBalance` (raw cash + day movements). Numbers were off by orders of magnitude. Fixed to read the latter.

`last_calc` is the broker-side timestamp of the last balance recalculation. The frontend localizes it to ART explicitly (`Intl.DateTimeFormat('es-AR', {timeZone: 'America/Argentina/Buenos_Aires'})`).

---

### 7.9 Portfolio (`/api/portfolio/*`) · port

Reads SQL-native from `portafolio.tenencia` and `portafolio.assets` (the `*API` mirror collections were eliminated). Valuation formula in `_valuacion_api(cantidad, precio, cartera, clase)`:

- Fixed income (`Títulos Públicos`, `Letras`, `ONs`, `Fideicomisos`, `CPD`) → `cantidad × precio / 100`
- Futures → `(precio + 1) × cantidad`
- FCI / others → `cantidad × precio`

| Method | Path | Summary |
|---|---|---|
| GET | `/aum` | AuM snapshots (historical) |
| GET | `/tasa-fija` | Tasa-fija bucket from latest AuM snapshot |
| GET | `/cer` | CER bucket from latest AuM snapshot |
| GET | `/fci-serie` | FCI history (rollup + per-issuer split) |
| GET | `/fci-snapshot` | FCI per-unit detail at a fecha |

Field schemas unchanged from v0.1; see `api/routers/carteras.py` and `_valuacion_api` for details.

---

### 7.10 Títulos (`/api/titulos/*`) · port

| Method | Path | Summary |
|---|---|---|
| GET | `/assets` | Instrument metadata |
| GET | `/flujos` | Unified cash flows (`mercado.curvas` + `mercado.bonds_master`) |

`flujos[]` element: `fecha`, `amortizacion` (% of VN for CER, absolute for bonds), `interes` (rate on residual for CER, absolute for bonds), `residual`.

---

### 7.11 Simulaciones (`/api/simulaciones/*`) · own

Hypothetical portfolios per user. Ownership is enforced inside the service (filter by `user_email`); 404 is returned both when the simulación does not exist and when it belongs to another user — does not leak existence.

| Method | Path | Summary |
|---|---|---|
| GET | `` | List the caller's simulaciones, newest first |
| POST | `` | Create with name + initial positions |
| GET | `/tickers` | Universe of tickers (Assets ∪ Curvas) for autocomplete. Public within the desk — no `user_email` filter. |
| GET | `/{simulacion_id}` | Read a simulación owned by the caller |
| PUT | `/{simulacion_id}` | PATCH semantics: only fields with non-null values are updated |
| DELETE | `/{simulacion_id}` | 204 on success, 404 on miss |
| POST | `/calcular` | Stateless recompute: cashflows + composición + métricas. No DB write. Body `{posiciones: [...]}`. |

Position schema: see `api/services/simulaciones.py::Posicion`.

---

### 7.12 News (`/api/news*`) · pub

Aggregates local + global headlines (RSS + Finnhub) into `news.headlines`. The reader mode (`/article`) fetches the target URL server-side; all URLs are validated against an anti-SSRF policy that blocks private, loopback, link-local, reserved and cloud-metadata addresses, plus non-HTTP(S) schemes and non-80/443 ports.

| Method | Path | Summary |
|---|---|---|
| GET | `/api/news` | Paginated headlines |
| GET | `/api/news/article` | Reader-mode extraction (`trafilatura`, 1 h cache) |
| GET | `/api/news/stats` | Per-source counts over last `horas` hours |

---

### 7.13 Market (`/api/market/*`) · pub

Global watchlist + calendar + OHLC.

| Method | Path | Summary |
|---|---|---|
| GET | `/quotes` | Watchlist quotes with 7d/MTD/YTD/1Y returns |
| GET | `/calendar/economic` | Finnhub economic calendar |
| GET | `/candle` | OHLC via Yahoo Finance |
| GET | `/profile` | Finnhub company profile |

---

### 7.14 Manager (`/api/manager/*`) · adm

Operational tooling. Every route requires the `manager` module (admin only by default). All reads pass through the SQL read pool (`core.postgres.get_pool()`); explicit mutations use the SQL write pool.

#### 7.14.1 Identity & RBAC management

| Method | Path | Description |
|---|---|---|
| GET | `/users` | List all users in `manager.users` |
| POST | `/users` | Body `{email, role, enabled?, notes?}` — creates or upserts |
| PATCH | `/users/{email}` | Partial update: `role`, `enabled`, `notes` |
| DELETE | `/users/{email}` | Remove |
| GET | `/roles` | Current matrix (DB or default) |
| PATCH | `/roles/{role}` | Body `{modules: [str]}` — replaces the module list for a role |
| GET | `/roles/audit?limit=` | Last N audit-log entries (`manager.role_audit`, newest first) |

Every mutation invalidates the in-process cache (`core/roles_sql.py::invalidate_cache`).

#### 7.14.2 Status

- `GET /status` — Unified state of motors (`TimeSales`, `MarketSnapshot`, `ForwardsLive`, `BreakevensLive`, `OptionsSnapshot`, `motor_ordenes`) and batch jobs (CER, DOLAR, AuM, Movimientos, Flujo). Staleness thresholds vary per probe; `estado ∈ {ok, lento, critico, fuera_rueda, sin_datos, atrasado, error_parse}`. Market hours = weekdays 10:00–17:05 ART.

#### 7.14.3 Checks

| Path | Description |
|---|---|
| `GET /checks/tasa-fija` | State of tasa-fija instruments in the latest AuM snapshot |
| `GET /checks/tickers-curvas` | `ticker_corto` index of `mercado.curvas` |
| `GET /checks/debug-soberano?ticker_corto=` | Inspect curve enrichment of a soberano |
| `GET /checks/breakevens-debug` | Trace the live breakeven aggregation |
| `GET /checks/futuros-dlr` | Diagnostics of the DLR outright curve enrichment |
| `GET /checks/debug-tna-futuros` | Per-outright comparison of TNA lineal vs TEA compuesta — used to validate the formula change of 2026-04-29 |
| `GET /checks/debug-curva-tea?ticker=` | Step-by-step recomputation of TEA / duration / convexity for one bond, exposing all intermediate inputs (trade, settlement, CER ratio, cashflow grid, XIRR). Mirrors `engines/curvas.py::calcular_campos`. |
| `GET /checks/discovery-pyrofex` | Reads `manager.pyrofex_discovery` — summary of every CFI code seen in `pyRofex.get_detailed_instruments()` with sample tickers. Used to identify CFI codes when extending motors. |
| `GET /checks/instruments-by-cfi?cficode=` | Drill-down: ALL instruments under one CFI from `manager.pyrofex_instruments` (currency, tickSize, contractMultiplier, putOrCall, strikePrice, etc.). |

#### 7.14.4 Jobs

| Method | Path | Limit | Description |
|---|---|---|---|
| POST | `/jobs/run` | 5/h, 20/d | Body `{tipo, args[]}`. Spawns a job (`tipo ∈ cashflow, bcra, cleanup_curvas, backfill_tasas, controles_datos` — ver `_CMDS` en `manager/jobs.py`). Worker is `subprocess.run` with 360 s timeout. |
| GET | `/jobs/{job_id}` | — | `status ∈ {running, done, error}`, `rc`, last 1500 chars of stdout/stderr |
| GET | `/jobs/history` | — | Runs from `manager.job_runs` (TTL 60 d). Filters: `tipo`, `status`, `desde`, `hasta`, `limit≤500` |
| GET | `/jobs/history/stats` | — | Per-tipo aggregates since `desde` (default 7 d) |

Route ordering: `/jobs/history` and `/jobs/history/stats` are declared before the catch-all `/jobs/{job_id}`.

#### 7.14.5 Options config

| Method | Path | Description |
|---|---|---|
| GET | `/options/expiries` | `{disponibles, activos, auto_pick, actualizado, mapa_size}` from `mercado.options_metadata` |
| PUT | `/options/expiries` | Body `{expiries: string[8]}`. Empty array → auto-pick mode. Engine reloads on next 5 min tick. |

#### 7.14.6 Asistente observability — *legacy, no en uso*

Endpoints leen `manager.asistente_logs`. El asistente (`/api/chat`) ya no se usa — se conserva el código (`api/agent/`) como referencia. Estos endpoints siguen vivos pero la tabla no recibe escrituras nuevas.

| Path | Description |
|---|---|
| `GET /asistente/stats?horas=` | Counts (`ok|error|truncated`), token usage, avg/p95 latency, estimated cost |
| `GET /asistente/logs?horas=&estado=&limit=` | Newest-first entries from `manager.asistente_logs` |
| `GET /asistente/timeseries?horas=` | Hourly buckets with `count`, `tokens`, `errors` |
| `GET /asistente/tools-ranking?horas=` | Tools ordered by invocation count with `ok`/`fail` |

#### 7.14.7 Logs

| Method | Path | Description |
|---|---|---|
| GET | `/logs?servicio=&lines=` | `journalctl` of an installed service. `servicio ∈ {api, motor_rofex, motor_curvas, motor_breakevens, motor_options, motor_dolar_mep, motor_caucion, motor_futuros_dlr, motor_ordenes, cloudflared}`. `lines ≤ 1000`. Light cache. |

#### 7.14.9 Intel ingest

PDF or pasted-text reports → Gemini JSON-mode extracts 12 macro variables → the last confirmed `IntelDoc` is injected into the assistant context.

| Method | Path | Description |
|---|---|---|
| POST | `/intel/extract` | `multipart/form-data` — `fuente` (required), optional `fecha`, `titulo`, `texto`, `pdf` (≤10 MB). Returns preview, **does not persist** |
| POST | `/intel/save` | JSON `{fuente, fecha, titulo, raw_text, extracted}`; writes `manager.intel_docs` with `confirmed=true` |
| GET | `/intel` | List newest-first (`limit≤200`, `fuente`) |
| GET | `/intel/latest` | Last confirmed report (used by `api/agent/context.py`) |
| GET | `/intel/{id}` | Fetch by row `id` |
| PATCH | `/intel/{id}` | Partial update |
| DELETE | `/intel/{id}` | Remove |

---

### 7.15 Chat (`/api/chat`) · chat — *legacy, no en uso*

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

`message`: 1–4000 chars. `history`: optional Claude-style content blocks.

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

`model_used` reports the model chosen by `decide_model()` (Haiku vs Sonnet). Errors follow the typed shape (§5). Every turn — success, truncation or error — is persisted to `manager.asistente_logs` (`/api/manager/asistente/*`).

`BLOCKED_PATH_PREFIXES` (portfolio / operaciones / cuentas / manager) are blocked from being invoked through the assistant tools — read-only market data only. Same policy applies to the MCP server.

> **Estado**: el asistente está desactivado a nivel producto. El código (`api/agent/`) y el endpoint se mantienen como referencia.

---

### 7.16 MM Workstation (`/api/mm/*`) · mm

**En reconstrucción 2026-05-04.** El backend MM (replay + backtest + paper trading vivo) se eliminó completo el 2026-05-04 — la nueva vista MM se construye desde cero sobre `mercado.order_book_l2` (motor `engines/order_book_l2.py`, hoy capturando `MERV - XMEV - AL30 - CI`) y `mercado.timesales`. Endpoints pendientes; el módulo `mm` (RBAC) y el prefix `/api/mm` (proxy Vercel) se mantienen para reusar.

---

### 7.17 Derivados Agro (`/api/derivados/agro*`) · admin-only inline

Tabla **PASE AGRO** (Trigo / Maíz / Soja Rosario). El backend lee snapshots live de `mercado.agro_snapshot` (escrito por `engines/motor_agro.py` — outrights FXXXSX filtrados por underlying agro), los joina con la fila PIZARRA manual (carga manual de la mesa, en SQL) y devuelve la tabla calculada. **Beta — restringido a `role == "admin"` mientras la mesa valida los números.** El router está montado bajo `_PUBLIC` para el routing pero ambos handlers chequean rol inline. Cuando se abra, el GET pasará a sales/trader y el PATCH a trader+admin.

| Method | Path | Summary |
|---|---|---|
| GET | `/api/derivados/agro` | Tabla completa (3 bloques: TRIGO, MAIZ, SOJA). Cada bloque trae 1 fila PIZARRA + 1 fila DISPO (placeholder) + N filas de futuros vivos. |
| PATCH | `/api/derivados/agro/pizarra/{commodity}` | Upsert de la fila PIZARRA. Body `{vencimiento_pizarra?, us_pizarra?}` (null = no tocar). Audit en SQL (`derivados` audit). |

**Cálculos puros (no se persisten):**

- `ars      = us × dolar_oficial_mid` (mid del UST$T mayorista MAE, vía `core.dolar_oficial.mid_oficial_live`)
- `pase     = us_pizarra − us_futuro`
- `tnav_us  = (us_pizarra / us_futuro)^(365/dias_a_vto) − 1` (compuesta)

Validado contra la planilla de la mesa: `(202.79/229.60)^(365/236) − 1 = -17.41%` (planilla -17.47%); `(190.00/191.90)^(365/149) − 1 = -2.41%` (planilla -2.41%).

El motor (`engines/motor_agro.py`) **no escribe `mercado.timesales`** (decisión consciente de la mesa — esta tabla sólo necesita el último precio). Filtra variantes paralelas (`*M`) y placeholders (`DISPO`) del universo descubierto. No persiste histórico diario por ahora.

---

### 7.18 MCP server (`/mcp`)

Read-only re-exposure of 30 analytical tools (curvas, forwards, breakevens, opciones, REM, macro, descomposición, sensibilidad, fair value). Independent OAuth 2.1 + PKCE + DCR layer on top of CF Access — does NOT use the bearer API key. Full doc: **`docs/MCP.md`**, tool reference: **`docs/MCP_TOOLS.md`**.

---

## 8. Data architecture

Las tablas SQL (Postgres/Supabase) son el system of record. Tenencias y catálogo
de títulos viven en `portafolio.tenencia` / `portafolio.assets`; las colecciones
espejo `*API` fueron **eliminadas** (2026-06-06) y la API lee las fuentes directo
(el join Curvas+BondsMaster lo hace `api/services/titulos_flujos.py`). El resto lo
escriben motores (real-time) y jobs SQL-native (vía `core.pg_mirror`).

| Table | Source | Writer |
|---|---|---|
| `mercado.*` | — | Motors write real-time |
| `mercado.caucion_snapshot` | — | `engines.caucion` |
| `mercado.futuros_dlr_snapshot` | — | `engines.futuros_dlr` |
| `mercado.agro_snapshot` | — | `engines.motor_agro` (snapshot only — no timesales) |
| `derivados.agro_pizarra` (+ audit) | — | `PATCH /api/derivados/agro/pizarra/{commodity}` |
| `mercado.options_*` | — | `engines.options` + `jobs.options_rollup` |
| `valuaciones.dolar_snapshot` | — | `engines.dolares` (live, `_id='current'`) |
| `valuaciones.dolar` | — | `engines.dolar_mep` (cron, histórico) |
| `news.headlines` | — | `jobs.news_ingesta`, `jobs.news_finnhub` |
| `market.quotes` | — | `jobs.market_quotes`, `jobs.market_anchors` |
| `manager.job_runs` | — | Background writers + TTL 60 d |
| `manager.pyrofex_discovery` / `pyrofex_instruments` | — | `scripts.discovery_pyrofex` (one-shot manual) |
| `manager.intel_docs` | — | `/api/manager/intel/*` |
| `manager.users` / `role_matrix` / `role_audit` | — | `/api/manager/users`, `/api/manager/roles`, auto-register on first visit |
| `operaciones.ordenes_live` / `ordenes_audit` | — | `motor_ordenes` (WS order_report) |
| `operaciones.operativas_mep` / `triggers_mep` | — | `/api/operativa/*` + scanner asyncio in `api.main` lifespan |
| `mcp.oauth_clients` / `oauth_codes` / `oauth_tokens` | — | OAuth 2.1 provider in `api/mcp/oauth.py` (TTL automático) |

---

## 9. Project structure

```
api/
├── main.py                  # FastAPI app, lifespan (sampler + triggers scanner), middlewares, RBAC dependency wiring
├── auth.py                  # CF Access JWT validation, get_user_email (3 ramas), require_module factory
├── ratelimit.py             # SlowAPI Limiter, per-identity key_func
├── cache.py                 # @cached(ttl=N) decorator (in-process)
├── db.py                    # get_db_* helpers (no FastAPI import)
├── deps.py                  # verify_api_key + db helper re-exports
├── services/                # pure-Python service layer
│   ├── cotizaciones.py      # listar_curva, snapshots, forwards, breakevens, caucion, futuros-dlr, …
│   ├── macro.py             # obtener_serie_macro, clasificar_nivel
│   ├── argy.py              # ARGY panel
│   ├── analitica.py         # canje, carry-trade, descomposicion-retorno, rolldown
│   ├── descomposicion_retorno.py
│   ├── sensibilidad.py
│   ├── fair_value.py
│   ├── rem.py
│   ├── opciones.py / derivados.py / repo.py
│   ├── ordenes.py           # send_order / cancel_order / list_orders_dia (pyRofex REST)
│   ├── operativa_mep.py     # crear_operativa, get_cotizaciones, listar_operativas_dia, serie_mep_minuto
│   ├── triggers_mep.py      # crear_trigger / cancelar / scanner_loop (asyncio)
│   ├── risk.py              # account_report, account_positions, account_detailed_position, saldo_para_rueda
│   ├── simulaciones.py      # CRUD + calcular (stateless)
│   ├── portfolio.py / renta_fija.py / canje.py / carry_trade.py
│   ├── derivados_agro.py    # PASE AGRO (pizarra manual + futuros live)
│   ├── debug_curva.py       # Recompute paso-a-paso de TEA/duration (manager/checks)
│   └── …
├── agent/                   # LLM assistant — legacy, no en uso
├── mcp/                     # MCP server (docs/MCP.md is authoritative)
└── routers/
    ├── analitica.py             # /api/analitica/*       (pub)
    ├── carteras.py              # /api/portfolio/*       (port)
    ├── chat.py                  # /api/chat              (chat)
    ├── cotizaciones.py          # /api/cotizaciones/*    (pub)
    ├── cuentas.py               # /api/cuentas/*         (op)
    ├── derivados_agro.py        # /api/derivados/agro    (pub + admin-only inline)
    ├── manager/                 # /api/manager/*         (adm)  — paquete con sub-routers
    │   ├── status.py
    │   ├── checks.py
    │   ├── jobs.py
    │   ├── options.py
    │   ├── asistente.py
    │   ├── logs.py
    │   ├── users.py
    │   └── roles.py
    ├── market.py                # /api/market/*          (pub)
    ├── me.py                    # /api/me                (own)
    ├── news.py                  # /api/news*             (pub)
    ├── operaciones.py           # /api/operaciones/*     (op)
    ├── operativa.py             # /api/operativa/*       (opr)
    ├── ordenes.py               # /api/ordenes/*         (opr)
    ├── risk.py                  # /api/risk/*            (opr)
    ├── simulaciones.py          # /api/simulaciones/*    (own)
    └── titulos.py               # /api/titulos/*         (port)
core/
├── postgres.py              # SQL pool (rw + ro). Pool compartido — nunca .close().
├── pg_mirror.py             # SQL-native writes (upserts por dominio)
├── roles_sql.py             # manager.users / role_matrix / role_audit, get_user_role, has_access
├── rofex_orders_session.py  # ensure_session_envio (idempotente, thread-safe)
└── …
```

---

## 10. Deployment

Systemd unit `api.service` on the DigitalOcean droplet (path `/root/TradingAV`, venv `/root/TradingAV/venv/bin/python`). Uvicorn binds to `127.0.0.1:8000`; Cloudflare Tunnel (`cloudflared.service`) publishes it as `api.acaquant.com`.

At startup the `lifespan` hook:
1. Warms up the Postgres pool (`core.postgres.get_pool()`).
2. Starts the resource sampler (`asyncio.Task`).
3. Starts the **MEP triggers scanner** (`asyncio.Task` running `evaluar_y_disparar_pendientes` + `cancelar_pendientes_eod` every 1 s).

Shutdown cancels both tasks cleanly.

```bash
# Reload after deploy
sudo systemctl restart api.service
journalctl -u api.service -f
```

The Postgres connection pool (`core.postgres.get_pool()`) is a shared, process-wide pool. Never tear it down (`.close()`) on the singletons — it kills the pool for every caller.

---

## 11. Testing

```bash
pytest -ra                              # unit tests (no DB)
pytest tests/unit/test_black_scholes.py # single file
pytest -m integration                   # requires Postgres — excluded by default

python -m scripts.perf_scan             # static analysis for SQL anti-patterns
```

CI (`.github/workflows/ci.yml`): ruff + perf_scan + pytest on every push.

---

## 12. Changelog

| Date | Change |
|---|---|
| 2026-04-15 | Initial release: `/api/health`, `/api/cuentas/*`, `/api/operaciones/*` |
| 2026-04-16 | Add `/api/portfolio/*`, `/api/titulos/*`, `/api/cotizaciones/*` |
| 2026-04-19 | Add `/api/chat` (Claude tool-use), `/api/manager/intel/*`, `/api/manager/resources*` |
| 2026-04-20 | Tier 1 security: SSRF, CF JWT validation, `require_manager`, rate limiting |
| 2026-04-20 | Tier 2 refactor: extract `api/services/*`, remove HTTP loopback agent→API, `@cached` |
| 2026-04-20 | Add `/api/analitica/*` (Tier 1 assistant tools over HTTP) |
| 2026-04-20 | `api/auth.py` accepts service-token JWTs from acaquant-web SSR; `CF_TRUSTED_SERVICE_TOKENS` |
| 2026-04-21 | Typed error model, `/api/cotizaciones/caucion`, `/futuros-dlr`, `/argy`, `convexity`, snapshot-curva-historico, pendiente-curva, liquidez-secundario |
| 2026-04-22 | Add `/api/me` and full **RBAC** (`manager.users`, `manager.role_matrix`, `manager.role_audit`, `require_module`). Frontend nav and `proxy.ts` consume `/api/me`. |
| 2026-04-22 | Add `/api/manager/users`, `/api/manager/roles`, `/api/manager/roles/audit`, `/api/manager/logs` |
| 2026-04-23 | Add `/api/cotizaciones/forwards-zscore`, `/fair-value*`, `/rem*`, `/historico/dolares` |
| 2026-04-23 | Add `/api/analitica/sensibilidad-retorno`, `/canje`, `/carry-trade`, `/descomposicion-retorno`, `/rolldown-esperado`, `/estrategia-historico` |
| 2026-04-24 | Add `/api/simulaciones/*` (CRUD + `/calcular` + `/tickers`) |
| 2026-04-25 | Add MCP server (`/mcp`) with OAuth 2.1 + PKCE + DCR; doc in `docs/MCP.md` |
| 2026-04-26 | Add `/api/ordenes/*` + `motor_ordenes` (REST send/cancel + WS order_report) |
| 2026-04-27 | Add `/api/operativa/*` (compra MEP de 1 click) and `/api/risk/*` (account balances) |
| 2026-04-28 | Add MEP triggers (`/api/operativa/mep/trigger*`), TP/SL bracket, `/api/operativa/mep/timesales` |
| 2026-04-28 | **fix(rbac):** `core/roles.py` no longer maps `service:*` to `admin` (was a privilege-escalation bug). Frontend now propagates `X-Acaquant-User-Email` because CF strips the CF-prefixed email header on service-token requests. |
| 2026-04-28 | **fix(operativa-mep):** `usd_efectivo`/`mep_efectivo` now apply `PRICE_FACTOR_BONOS` (BYMA "per 100 VN"). Series `/timesales` returns real UTC with explicit tz. |
| 2026-04-28 | **fix(ordenes):** `send_order` always initializes pyRofex (was conditional on `account is None` — broke trigger scanner with `Environment not specify.`). |
| 2026-04-28 | **fix(risk):** `/account/saldo` reads `currencyBalance.detailedCurrencyBalance` (Primary's "Efectivo Disponible"), not `availableToOperate.cash` (post-margin). |
| 2026-04-28 | **rbac:** Split del módulo `operar` — antes todas las acciones de trading caían bajo `operaciones`; ahora `/api/ordenes`, `/api/operativa`, `/api/risk` están bajo `operar` (admin + trader + sales) y `/api/operaciones`, `/api/cuentas` quedan en `operaciones` (admin + trader). Sales puede operar pero NO ver la mesa de flujos. |
| 2026-04-29 | Add `/api/mm/*` — MM Workstation backend: replay (`/trades-dia`, `/fechas`), paper trading vivo (`/live-snapshot`) y backtest sweep (`POST /backtest`). Módulo `mm` (admin only por default). |
| 2026-04-29 | **fix(dolar):** Dólar oficial pasa al feed MAE mayorista (UST$T plazo 000) vía script local en PC oficina (`valuaciones.dolar_oficial_live`). dolarapi.com queda solo para series históricas. Filtro estricto Mayorista plazo 000 (= A3500 spot). Watchlist `/argy` y `/api/cotizaciones/futuros-dlr` consumen el mismo mid. |
| 2026-04-29 | **fix(futuros-dlr):** `tasa_implicita_tna` cambia de TEA compuesta a **TNA lineal** `(precio/spot − 1) × 365/dias` para alinearse con la convención del terminal Rofex / la mesa. Diverge 2-4 puntos de la TEA en vencimientos largos. |
| 2026-04-29 | **fix(curvas):** Branch `tasa_fija` de `engines/curvas.py` pasa a usar **settlement T+1** como base para `dias_a_vto` y filtro de cashflows (antes era `fecha_trade`). Alinea con calculadora local de la mesa (15 días settle vs 16 trade). |
| 2026-04-29 | Add `/api/manager/checks/debug-tna-futuros`, `/checks/debug-curva-tea`, `/checks/discovery-pyrofex`, `/checks/instruments-by-cfi`. Discovery persiste en `manager.pyrofex_discovery` (summary 20 samples/CFI) y `manager.pyrofex_instruments` (full detail por CFI). |
| 2026-04-29 | **fix(rbac):** `/api/titulos/*` pasa de `_PORTFOLIOS` a `_PUBLIC` — sales no podía ver la curva de renta-fija ("MERCADO CERRADO") porque el catálogo no devolvía `flujos`. `titulos` es puro catálogo, no info de portfolio. |
| 2026-04-29 | Add MM Workstation Manager **ASSETS tab** — selector CFI + selector underlying + tabla con todos los instruments del mercado (drill-down de discovery pyRofex). |
| 2026-04-29 | Add `/api/derivados/agro` + `engines/motor_agro.py` — Pase Agro (Trigo/Maíz/Soja Rosario, CFI FXXXSX). Tabla con fila PIZARRA editable manual (admin only beta) + futuros live de `mercado.agro_snapshot`. TNAV = `(pizarra/last)^(365/dias) − 1` validada contra planilla mesa. Motor NO escribe timesales (solo snapshot). |
| 2026-05-04 | **refactor(MarketSnapshot):** `engines/valores.py` y `engines/curvas.py` ahora escriben con upsert `$set` parcial sin pisarse. `motor_curvas` deja de enriquecer `mercado.timesales` por trade — sólo escribe a `mercado.market_snapshot.metrics.{TEA,TEM,duration,mod_duration,convexity,paridad}`. 5 consumers migrados de agregación `sort+group` sobre timesales a lectura directa sobre `mercado.market_snapshot.metrics`. `top_trades`/`recent_trades` removidos (payload muerto). |
| 2026-05-04 | Add **Order Book L2** — `engines/order_book_l2.py` (sesión rofex separada, append-only) + `mercado.order_book_l2`. Endpoint `GET /api/cotizaciones/order-book-historico`. 4 tools nuevas en MCP (`order_book`, `order_books_curva`, `order_book_historico`, `listar_tickers_orderbook_l2`). Cron L-V 13:00–20:05 UTC. |
| 2026-05-04 | **refactor(históricos):** `get_historico_curva` y `snapshot_curva_historico` migrados de agregación sobre timesales a lectura sobre `mercado.snapshots_cierre` (cierre diario pre-agregado por `jobs/snapshot_cierre`). Backfill a 5 curvas hasta 2026-04-30 vía `scripts/backfill_snapshots_cierre`. |
| 2026-05-04 | **fix(retorno-total + carry-trade):** Live fallback a `mercado.market_snapshot.metrics` cuando la fecha pedida es hoy y el cron 20:25 UTC aún no corrió. Aplicado a `snapshot_curva_historico`, `_precios_diarios_curva` (carry) y `get_historico_curva`. Frontend Vercel: `/api/historico-curva` y `/api/analitica/[...path]` con `dynamic="force-dynamic"` + `Cache-Control: no-store` para que el CDN no sirva la respuesta vieja. |
| 2026-05-04 | **wipe(MM Workstation):** Borrón completo del backend MM (replay/backtest/live-snapshot) y del frontend MM (acaquant-web). Eliminados `api/services/mm.py`, `api/routers/mm.py`, `tests/unit/test_mm.py`, `docs/mm_workstation.jsx` y los componentes en `acaquant-web/src/{components,app}/mm`. Módulo `mm` (RBAC) y prefix `/api/mm` (proxy Vercel) se mantienen para reusar. La nueva vista MM se construye desde cero sobre `mercado.order_book_l2` + `mercado.timesales`. |
| 2026-05-05 | **feat(MM Microstructure):** Nueva vista `/mm` en acaquant-web con 4 tabs (Intraday, Impact b/k, Smile U, Stylized Facts) + tooltips explicativos `?` en cada métrica. Backend `api/services/mm_microstructure.py` con cap 1-4 del libro Cartea/Jaimungal/Penalva sobre `mercado.order_book_l2` + `mercado.timesales`. 6 endpoints en `/api/mm/*`. |
| 2026-05-05 | **migrate (histórico — etapa Mongo):** `mercado.timesales` y `mercado.options_data` (entonces colecciones Mongo) migradas a Time Series Collections (granularity=seconds, metaField=ticker/symbol). Compresión 74% y 98% on-disk respectivamente. Scripts en `scripts/migrate_timesales_swap.py` y `scripts/migrate_opciones_data_swap.py` con modos precheck/swap/validate/rollback/cleanup. (Evento histórico previo al decomiso de Mongo — ambas tablas viven hoy en SQL.) |
| 2026-05-05 | **wipe:** Borrado de todo el aparato dolarapi.com — `core/dolar_api.py`, `jobs/dolar_api.py`, `tests/unit/test_dolar_api.py`. Migrados `engines/curvas.py` (`cargar_a3500_actual`) y `serie_macro("dolar_oficial"/"mayorista")` al feed MAE / `macro.series_macro` (serie DOLAR). `dolar_blue` queda sin fuente. Cron `dolar_api` apagado. |
| 2026-05-05 | **feat(operaciones-negocio):** MVP de la vista NEGOCIO en `/operaciones`. Service compartido `api/services/aunesa_negocio.py` con parseo + categorización (16 categorías: compra/venta/FCI super y bilateral/acreencia/4 sub-cauciones/depósito/extracción/etc) + dedup específico (DIF/DIS bilaterales, multi-moneda en dividendos, uso=GRAL en FCI super) + inversión de signo broker→cliente. Job `jobs/negocio_movimientos.py` (cron horario 15-22 UTC L-V) persiste boletos consolidados en `operaciones.negocio_movimientos` (idempotente por `(fecha, comprobante)`). 2 endpoints `/api/operaciones/negocio` y `/negocio/fechas`. Frontend tab NEGOCIO con cards por categoría, top 20 tickers y tabla detallada. **No expuesto al asistente ni al MCP** (policy datos privados de mesa). Detalle: `docs/sesion_2026_05_05_negocio.md`. |
| 2026-08-11 | **feat(financiamiento):** Tab **FINANCIAMIENTO** en `/operaciones` (NEGOCIO) + `GET /api/operaciones/financiamiento`. Libro VIVO de pagarés/cheques: assets con `cartera='FINANCIAMIENTO'` y vencimiento HOY o posterior, al grano cuenta × instrumento. **Nominal y tasa, nunca bruto** (se compran con descuento y la mayoría son dólar-linked liquidados en pesos → el importe pagado no compara entre filas). La cantidad sale de `portafolio.tenencia` (posición) y la tasa de `operaciones.operaciones.tasa` de los boletos MAV, matcheada por (`id_cuenta`, código del corchete de `negocio_movimientos.informacion`); sin match la tasa queda `null` y se muestra vacía. Vista 2×2 al 50% (Σ cantidad por comitente · cantidad+tasa por instrumento · barras Σ cantidad por vencimiento · panel reservado) con cross-filter 3-way client-side. Service `api/services/financiamiento.py`, tests `tests/unit/test_financiamiento.py`, diag `scripts/diag_financiamiento.py`. |
| 2026-08-11 | **feat(financiamiento):** HD/DL, perf y modo claro de la tab FINANCIAMIENTO. (1) **CLASE_ACTIVO HD/DL** — regla nueva `financiamiento_clase` en `jobs/assets_autofill.py`: infiere por NOMINAL de la última tenencia (≤ 5.000 → HD, > → DL). Única regla heurística del job; existe porque HD y DL son escalas distintas (5.000 vs 27.000.000) y graficarlas juntas deja al HD invisible. **No pisa lo cargado a mano** (invariante del job) → la máquina bootstrapea, el humano corrige en Manager → ASSETS. Backfill y cron son el mismo comando. La vista pasó a filtrar por `clase` (una por vez, nunca sumadas) en vez de por `tenencia.moneda`, que queda informativa. (2) **PERF** — índice `ix_tenencia_fecha_unidad`: la vista arranca por instrumento (filtra `fecha`, joinea `unidad`) y `ix_tenencia_cuenta_fecha` no le servía (columna líder `id_cuenta`) → era un seq scan de la tenencia histórica entera. Además la query de tasas se acotó a las cuentas con financiamiento vivo (antes agregaba TODOS los boletos MAV de la historia para descartar la mayoría en Python). (3) **Modo claro** — los paneles llevan `bg-[var(--t-panel)]` + separadores de header/filas: sin fondo blanco sobre el gris de página los cuatro paneles se fundían en una sola mancha (en oscuro no se notaba, #000 vs #080808). (4) Se quitó el toggle **SOLO AUM** (no aportaba claridad). |
| 2026-08-11 | **fix(financiamiento):** el umbral HD/DL era **5.000** y mandó a DL cientos de pagarés que son HD (200k-613k nominales). Corregido a **5.000.000**. La evidencia son los nominales reales y sus tasas (`core/mav_tasa.py`): 30.000 @ 7%, 100.000 @ 6% y 500.000 @ -0,5% son tasas de DÓLAR; 27.000.000 @ 39,5% es tasa de PESOS → la frontera está entre 500.000 y 27.000.000. Señal de diagnóstico que lo delató: **260 assets en DL contra un puñado en HD** — cuando un lado se lleva todo, el umbral está mal puesto. Como el job NUNCA pisa un valor ya escrito (su invariante), re-correrlo NO corrige lo mal clasificado: va `scripts/fix_financiamiento_clase.py` (dry-run por default, `--aplicar` para escribir), que solo toca lo que lleva la huella del umbral viejo Y cuyo último escritor fue el job — lo editado por un humano se reporta y no se pisa. Tests: el valor del umbral queda PINEADO (`test_el_umbral_es_5_millones`) y los nominales reales se congelan del lado que dice su tasa. |
