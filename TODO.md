# TODO — Oportunidades de mejora pendientes

Salida del análisis exhaustivo 2026-04-20. Lo que quedó sin implementar
por alcance: requiere rediseño, múltiples archivos, o validación previa
con el equipo. Cada ítem tiene contexto para retomar después.

Notación: `[B]` backend TradingAV, `[F]` frontend acaquant-web.

---

## Backend

### [B3] Invalidar caches locales de `api/routers/carteras.py`

Los caches `_assets_enrich_cache` y `_fci_assets_cache_data` tienen TTL
de 10 min. Cuando `jobs/carteras.py` sincroniza nuevos assets desde
Aunesa, la API sigue sirviendo la foto vieja hasta que expire el TTL.

**Opciones**:
- A. Reducir TTL a 60s (trivial, menos hit rate pero siempre fresco).
- B. Exponer endpoint admin `/api/manager/cache/clear` que invalide
  tanto `api.cache._store` como los caches locales por módulo.
- C. Mongo change streams (overkill para este tamaño).

Recomendado: A + B.

### [B6] Unificar logs en `Manager.Events`

Hoy conviven 4 streams de eventos paralelos:
- `/root/TradingAV/logs/*.log` (archivo)
- `Manager.ChangeLog`
- `Manager.AsistenteLogs`
- `Manager.JobRuns`

**Propuesta**: colección unificada `Manager.Events` con shape:
```
{ source: "cron"|"asistente"|"changelog"|"api", tipo, when, payload }
```

Cada uno conserva su wrapper (JobRunLogger, etc) pero persiste a
`Manager.Events`. Un solo endpoint `/api/manager/events` cubre todo
con filtros. Índices `(source, when)`, `(tipo, when)`.

### [B9] Auditar TTLs de cache por endpoint

TTLs actuales:
- `/portfolio/resumen`: 300s
- `/portfolio/aum`: 300s
- `/cotizaciones/opciones`: 10s
- `/portfolio/cer`: 300s

Pero los productores tienen cadencias muy distintas:
- `Carteras` se actualiza 3×/día → 300s está bien
- `MarketSnapshot` cada 1s → no tiene cache
- Live de forwards/breakevens → 30s proxy ISR + 0s API, inconsistente

**Acción**: mapear cada endpoint a `refresh_rate_productor` y alinear.

### [B10] Resolver findings restantes de `perf_scan`

Quedan 4 PERF001 (find sin projection) en engines y otros menores.
Ninguno catastrófico pero ocupan bandwidth Atlas.

```
engines/breakevens.py:49
engines/curvas.py:103, :314
engines/forwards.py:34
```

Agregar projections `{_id:0, ticker:1, ...}` según uso real.

### [B11] Split `Opciones.Metadata` como KV store

Hoy es un único doc `{type:"config"}` con tasa + expiries +
expiries_disponibles + expiries_updated_by_ui. Mezcla config humana
con estado del engine.

**Propuesta**: 2 docs por tipo:
- `{type:"user_config"}` → tasa, expiries (inputs del user)
- `{type:"engine_state"}` → expiries_disponibles, last_refresh, mapa_size

Separar lecturas/writes y hacer auditable qué puso quién.

### [B13] Instrumentar todos los jobs con `JobRunLogger`

Hoy solo `jobs/carteras.py` usa `core.job_runs.JobRunLogger`. Los ~14
jobs restantes escriben a archivos `.log`. Migrar:

- `jobs/aum.py`, `aum_backfill.py`, `aum_resumen_fci.py`
- `jobs/cashflow.py`, `flujo_contrapartes.py`
- `jobs/bcra.py`, `volatilidad_ggal.py`
- `jobs/options_rollup.py`, `cleanup_curvas.py`
- `jobs/news_ingesta.py`, `news_finnhub.py`, `economic_calendar.py`
- `jobs/market_quotes.py`, `market_anchors.py`
- `jobs/sync_api_copies.py`

Cada uno es copy-paste del patrón ya existente en `carteras.py`. Una
vez migrados, la tab JOBS del Manager muestra el historial completo.

### [B14] Healthcheck más rico

`/api/health` devuelve `{status: ok}`. Podría devolver:
- `mongo_rw_ping_ms`, `mongo_read_ping_ms`
- último write en `Trading.TimeSales` (lag real del motor)
- última corrida de cada job en `Manager.JobRuns`
- contadores de memoria Atlas

Usar en Cloudflare healthchecks + alerts (UptimeRobot, etc).

### [B15] Feature flags en `Manager.FeatureFlags`

Hoy no hay manera de togglear features sin deploy. Casos de uso:
- "ocultar Big Tech en watchlist hasta que Finnhub lo pagan"
- "auto-purgar Opciones.Data cada X días"
- "habilitar streaming en /api/chat (beta)"

Esquema simple: `{flag: "ship_streaming_chat", enabled: bool, updated_at}`.
Cache in-proc con TTL 30s. Endpoint `/api/manager/flags` GET+PUT.

### [B16] Rate limit por API key

`API_KEY` es global. Si se filtra cualquiera puede DoS. Un contador
simple por token + ventana temporal en `Manager.APIUsage` (counter
++ por request, TTL 1h) da auditoría + futura protección con policy.

---

## Frontend

### [F1] Endpoint agregado `/api/renta-fija/all`

`/renta-fija/page.tsx` hace 6 fetches en paralelo cada navegación. Con
revalidate distintos (10/30/600/30/300/300s).

**Propuesta**: un único endpoint backend que haga las 6 queries
server-side en paralelo (ya están los helpers en routers distintos) y
devuelva un paquete. El frontend hace 1 fetch, 1 proxy hop.

También aplicable a `/derivados` (libro + opciones) y `/portfolios`.

### [F2] Consolidar 18 proxies en `/api/[...path]` único

Hoy hay un proxy catch-all para `/api/manager/*`, pero el resto tiene
un `route.ts` dedicado por endpoint:

- `/api/trades`, `/api/contrapartes`, `/api/cashflow`
- `/api/portfolio/detalle`, `/resumen`, `/cer`, `/tasa-fija`
- `/api/flujo-vs-aum`, `/api/market/quotes`, `/api/market/calendar`
- `/api/opciones-meta`, `/api/historico-curva`, `/api/aum-fci/*`
- `/api/news`, `/api/news/article`

Casi todos son clones del patrón: forward con `apiFetch`, mismo auth,
mismo manejo de error. Consolidar en un único `/api/[...path]/route.ts`
deja solo excepciones con lógica custom (`/api/chat` streaming,
`/api/news/article` trafilatura).

**Tradeoff**: perdés revalidate por ruta individualmente. Se puede
pasar como query param (`?ttl=30`) o header, o mantener los casos con
TTL específico como excepción.

### [F3] Split `manager-view.tsx` (805 líneas)

Es un único archivo con 7 tabs. Cada edit recompila todo. Tree-shaking
no saca nada.

**Propuesta**: 1 archivo por tab con lazy:
```
components/manager/tab-diagnostico.tsx
components/manager/tab-backfills.tsx
components/manager/tab-validaciones.tsx
components/manager/tab-historial.tsx
components/manager/tab-latencia.tsx
```

`manager-view.tsx` queda como dispatcher con `dynamic()` imports. Ya
hecho para JOBS (`jobs-runs-panel.tsx`) como primer paso.

### [F4] Split `intel-panel.tsx` (545 líneas)

Mismo caso — mezcla lista de docs + formulario upload + preview PDF +
extracción. Candidatos:
```
components/intel/intel-list.tsx
components/intel/intel-upload-form.tsx
components/intel/intel-preview.tsx
components/intel/intel-extraction-editor.tsx
```

### [F5] SWR / TanStack Query para poll + dedup

`useEffect + fetch + setState` repetido en:
- `watchlist-panel.tsx` (poll 30s)
- `news-panel.tsx`
- `asistente-dashboard.tsx`
- `jobs-runs-panel.tsx`
- otros

Con SWR gratis: dedup entre componentes (si 2 paneles piden las mismas
quotes, 1 request), revalidate-on-focus, retry exponencial, cache cross
navigation. Reduce ~150 líneas de boilerplate.

Propuesta: `npm i swr` + wrapper `useApi<T>(path, opts)` que envuelve
`useSWR` con `apiFetch` como fetcher.

### [F9] TradingView chart: cambio de símbolo sin recargar

Hoy `tradingview-chart.tsx` destruye y re-crea el widget cuando cambia
`symbol` (key change). ~500ms de flash.

TradingView widget expone `widget.setSymbol(symbol, interval, callback)`.
Si guardamos la ref del widget en `useRef`, al cambiar symbol llamamos
`setSymbol` en vez de re-montar. UX mucho más fluida.

### [F10] Design tokens + Tailwind theme extension

Colores `#080808`, `#1a1a1a`, `#ff9900`, `#00cc66`, `#ff3333`, `#ff9900/10`
se repiten en casi cada componente. Un cambio de tema (ej. modo claro,
o rebranding) requiere sed global.

**Propuesta**: `tailwind.config.ts` con `theme.extend.colors` mapeados
a variables CSS en `globals.css`:
```css
:root {
  --bg-primary: #080808;
  --bg-panel: #0a0a0a;
  --accent: #ff9900;
  --success: #00cc66;
  --error: #ff3333;
}
```

Después `bg-primary`, `text-accent`, etc.

### [F11] Migración completa de tipos a `lib/types.ts`

Ya creado. Faltan migrar los componentes que duplican tipos localmente:
- `renta-fija-table.tsx`
- `top-ticker.tsx`
- `libro-panel.tsx`
- `curvas-chart.tsx`
- `breakevens-block.tsx`
- `breakeven-chart.tsx`
- `forwards-panel.tsx`

Es sed mecánico: `interface X` → `import { X } from "@/lib/types"`.

### [F12] Streaming en `/api/chat`

Hoy el endpoint es bloqueante: espera todos los tool calls (MAX_STEPS=6)
y devuelve la respuesta final. Percepción del usuario: "se cuelga" por
5-15s.

**Propuesta**: SSE. Eventos en vivo:
- `step_start` { step, model_used }
- `tool_call` { name, args }
- `tool_result` { name, summary }
- `text_delta` { chunk }
- `done` { usage, elapsed_s }

Frontend pinta "razonando...", "consultando forwards...", etc. Latencia
percibida -60%.

Requiere: backend FastAPI con `StreamingResponse`, Claude/Gemini
provider con streaming, frontend con `EventSource` o fetch + reader.

### [F13] `<DataTable>` genérico

Cada tabla (renta-fija, jobs, watchlist, opciones, histórico) reinventa
la misma lógica: headers, sort on click, filter input, paginación,
estilo border/bg dark.

**Propuesta**: 1 componente `<DataTable columns={} data={} />` con:
- `columns: { key, label, align, formatter?, sortable? }[]`
- sort state interno
- filter input opcional
- empty state, loading state

Reduce ~500 líneas de código repetido.

### [F14] Pasar páginas grandes a Server Components

Muchas páginas son `"use client"` inútilmente. Ej. `/portfolios` tiene
el selector de cuenta client-side pero la data se podría fetchear
server-side y pasar como prop. Reduce JS enviado al browser.

Buenos candidatos: portfolios, aum, retorno, operaciones.

### [F15] Error tracking (Sentry / Axiom)

Hoy si algo rompe en prod el usuario lo ve en pantalla y nadie se
entera. `error.tsx` global (ya agregado) muestra un fallback pero no
reporta. Sentry free tier es suficiente.

---

## Cross-cutting

### CI/CD

- Tests: hoy solo hay unit tests en `tests/unit/`. Sin e2e, sin smoke
  de API, sin tests del agente IA. CI corre `ruff check` + `pytest -ra`.
  Faltan:
  - Test de integración mockeando pyRofex (carga de instruments, etc).
  - Smoke HTTP contra la API levantada con `pytest` + `httpx`.
- Linting frontend: ESLint corre pero no falla CI. Agregar step.
- Type check: `tsc --noEmit` no corre en CI (Vercel sí lo hace pero
  queda sin validar en PR).

### Docs

- `CLAUDE.md` es la fuente de verdad pero está creciendo — considerar
  partir en `docs/ARCHITECTURE.md`, `docs/DEPLOY.md`, `docs/API.md`,
  etc. y que `CLAUDE.md` sea índice.
- Diagramas: un `docs/diagrams/` con flujos (mercado → motor → Mongo →
  API → frontend) rendered con mermaid. Ayuda al onboarding.

### Observabilidad prod

- Nada de metrics/tracing hoy (OpenTelemetry, Prometheus, etc).
- Para el tamaño actual es overkill, pero cuando escale sería útil:
  - p50/p95/p99 por endpoint
  - rate de errores
  - health del cluster Atlas (WiredTiger cache, locks, etc)
