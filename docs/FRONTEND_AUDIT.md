# Audit — acaquant-web

Fecha: 2026-04-21
Alcance: repo `acaquant-web` (Next.js 15 + React 19, App Router, Vercel).
No hay cambios de código aplicados. Sólo observaciones y recomendaciones.

---

## Resumen ejecutivo

El frontend está bien estructurado en términos generales: respeta el separation of concerns entre server components (SSR con `apiFetch`) y client components con polling propio, encapsula los secretos (API_KEY, CF service tokens) exclusivamente en server code, y usa ISR como estrategia principal de cache.

**Pero hay un problema grande y dos medianos:**

1. **🔴 AutoRefresh global cada 5 s** anula todo el sistema de ISR. Es el causante probable de la carga sostenida que dispara Bot Fight Mode de Cloudflare y presiona al Droplet (1 GB RAM). Es el fix más impactante del reporte.
2. **🟡 Inconsistencia de TTLs**: varias rutas usan `revalidate: 0` innecesariamente.
3. **🟡 `/api/manager/*` no está en el matcher del proxy**: double-gated por el backend, pero se desperdicia un roundtrip.

El resto son cuestiones de higiene: endpoints backend sin consumidor, duplicación menor en proxy routes.

**No se detectaron leaks de secretos al bundle cliente** — los `process.env.*` están todos en archivos server (routes, lib/api.ts, layout.tsx, proxy.ts). ✅

---

## 1. Hallazgos críticos (Tier A)

### A1 — AutoRefresh global anula el sistema de ISR

**Archivos**: `src/app/layout.tsx:40`, `src/components/auto-refresh.tsx`.

**Qué hace**:

```tsx
// src/app/layout.tsx
<AutoRefresh intervalMs={5000} />

// src/components/auto-refresh.tsx
useEffect(() => {
  const id = setInterval(() => {
    router.refresh();           // ← cada 5s
  }, intervalMs);
  return () => clearInterval(id);
}, [router, intervalMs]);
```

El componente se monta en el root layout, por lo que **corre en todas las páginas todo el tiempo**. `router.refresh()` de Next.js App Router **invalida la cache ISR y re-ejecuta todos los server components** de la ruta actual, disparando todos los `apiFetch()` que esos componentes hacen.

**Por qué importa**:

- En `/renta-fija`, cada refresh hace 6 fetches paralelos: renta-fija (TTL 10s), forwards (30s), flujos (600s), breakevens (30s), breakevens-hist (300s), forwards-hist (300s). Con `router.refresh()` cada 5s, el TTL de `flujos` (600s) se ignora 120 veces antes de expirar naturalmente. Cache rota.
- El `TopTicker` del layout hace 3 fetches (mep, dolar, renta-fija) cada 5 s → **2160 requests/h por usuario, sólo del ticker**.
- Los componentes client YA tienen su propio polling con intervalos correctos:
  - `news-panel.tsx`: 60 s
  - `watchlist-panel.tsx`: 30 s
  - `recursos-panel.tsx`: 60 s
  - `libro-panel.tsx`: 5 s (éste sí es ticker intradía)
  - `asistente-dashboard.tsx`: 10 s
  - `manager-view.tsx`: 10 s + 2 s para polls de jobs puntuales
- **AutoRefresh es redundante**: los componentes críticos ya se auto-actualizan.

**Consecuencias observables**:

- Load sostenido al backend durante las 12 h / día de operación (~720 refresh cycles / h / usuario).
- **Cloudflare Bot Fight Mode marcó a Vercel como bot** — fue lo que causó el 502 que diagnosticamos hoy. Estos volúmenes son atípicos para un navegador humano.
- Presión de RAM/CPU sobre el Droplet de 1 GB.
- Cache HIT rate bajo del `@cached` en el backend (el TTL expira antes de que vuelva a pegar).

**Recomendación**:

**Sacar `AutoRefresh` del root layout.** Los componentes que realmente necesitan refrescarse ya lo hacen por su cuenta, con intervalos elegidos deliberadamente.

Si querés preservar algún refresh global, hacelo granular:

```tsx
// Alternativa conservadora: sólo en páginas con datos live-tick
// renta-fija/page.tsx, derivados/page.tsx
<AutoRefresh intervalMs={30000} />   // 30 s en vez de 5 s
```

O mejor: borrar `AutoRefresh` y agregar polling específico en las 1-2 vistas que lo requieran. **Impacto estimado**: reducción del 70-80 % de tráfico Vercel→Backend cuando no se está clickeando.

---

## 2. Hallazgos medianos (Tier B)

### B1 — TTLs de ISR inconsistentes / desalineados con el dato

**Casos con `revalidate: 0` que no tienen justificación clara**:

| Ruta proxy | Endpoint backend | TTL frontend | TTL backend (@cached) | Frecuencia real del dato |
|---|---|---|---|---|
| `/api/portfolio/cer` | `/api/portfolio/cer` | 0 | 300 s | 1×/día (cron 23 UTC) |
| `/api/portfolio/resumen` | `/api/portfolio/resumen` | 0 | 300 s | 1×/día |
| `/api/portfolio/detalle` | `/api/portfolio/detalle` | 0 | 300 s | 1×/día |
| `/api/portfolio/tasa-fija` | `/api/portfolio/tasa-fija` | 0 | 300 s | 1×/día |
| `/api/market/quotes` | `/api/market/quotes` | 0 | — | ~30 min (cron) |
| `/api/market/calendar` | `/api/market/calendar/economic` | 0 | — | 1×/día |

Con `revalidate: 0`, cada request del browser pega al Vercel serverless function que pega al backend. El backend tiene `@cached` en algunos, pero eso no evita el roundtrip. El overhead es ~100-200 ms por request + bandwidth Vercel.

**Recomendación**: ajustar cada `revalidate` al ciclo real del dato:

- Portfolio: `revalidate: 300` (5 min) o `revalidate: 600`. Los valores no cambian durante el día.
- Market/quotes: `revalidate: 30`.
- Market/calendar: `revalidate: 600` o `3600`.

### B2 — `Cache-Control` header inconsistente

Algunas rutas devuelven `Cache-Control: s-maxage=N, stale-while-revalidate=M` (para el Vercel Edge CDN), otras no.

**Con `Cache-Control`**: `cashflow`, `contrapartes`, `flujo-vs-aum`, `historico-curva`, `opciones-meta`, `trades`, `aum-fci/serie`, `aum-fci/snapshot`.

**Sin `Cache-Control`**: `news`, `chat`, `market/calendar`, `market/quotes`, `news/article`, `portfolio/cer`, `portfolio/detalle`, `portfolio/resumen`, `portfolio/tasa-fija`, `manager/[...path]`.

Es una capa distinta de ISR (Edge CDN vs serverless cache). Cuando falta, Vercel no cachea en el edge y cada serverless invocation gasta cómputo.

**Recomendación**: política uniforme. Sugerencia:

- Rutas de datos live (market, news) → `s-maxage=30, stale-while-revalidate=120`.
- Rutas de portfolio → `s-maxage=300, stale-while-revalidate=600`.
- Chat y manager (dinámicos o admin) → dejar sin `Cache-Control`.

### B3 — `/api/manager/*` fuera del matcher del proxy.ts

**Archivo**: `src/proxy.ts`.

```tsx
export const config = {
  matcher: ["/manager/:path*", "/asistente/:path*", "/api/chat/:path*"],
};
```

La ruta `/api/manager/*` **no está en el matcher**. Los requests entran al catch-all `src/app/api/manager/[...path]/route.ts` y reenvían al backend.

**Impacto**: bajo.

- El backend tiene `require_manager` aplicado a todos los routers admin (ver `api/main.py`). Un non-admin que hace `curl` a `/api/manager/status` de forma autenticada pasa el proxy pero recibe **403 del backend**. Data segura.
- Pero se paga un roundtrip Vercel → CF → backend para rechazar. Se podría ahorrar gateando en el proxy.

**Recomendación**: agregar `"/api/manager/:path*"` al matcher de `src/proxy.ts`. Los non-admin reciben 403 antes del roundtrip.

---

## 3. Hallazgos menores (Tier C)

### C1 — Endpoints del backend sin consumidor en el frontend

Hice cross-reference de endpoints expuestos por la API vs llamadas a `apiFetch` / `fetch` desde el frontend. Los siguientes existen en el backend pero **nadie los consume desde acaquant-web**:

| Endpoint | Posible uso |
|---|---|
| `/api/cotizaciones/badlar` | Serie BADLAR (BCRA) — nadie la grafica |
| `/api/cotizaciones/cer` | Serie CER — nadie la grafica directo |
| `/api/cotizaciones/historico/mep` | Serie MEP — nadie la grafica |
| `/api/cotizaciones/historico/opciones` | Trades históricos de opciones — no usado |
| `/api/titulos/assets` | Metadata de títulos — no usado directamente |
| `/api/portfolio/carteras` | Carteras directas — usa agregadores (resumen/detalle) |
| `/api/portfolio/aum` | AuM directo — usa agregadores (fci-*, tasa-fija, cer) |
| `/api/market/candle` | OHLC Yahoo — no usado |
| `/api/market/profile` | Company profile Finnhub — no usado |
| `/api/news/stats` | Stats de fuentes news — no usado |
| `/api/analitica/*` | Tool del asistente — exposición HTTP para debug |

**Nota**: varios pueden tener uso legítimo (testing manual, integraciones externas, features en roadmap). No son necesariamente código muerto.

**Recomendación**: pasar por este listado y o bien (a) documentar explícitamente su propósito, (b) eliminarlos si no hay plan, o (c) agregar un comentario `# public API, no frontend consumer` en el router.

### C2 — Duplicación en proxy routes

`src/app/api/chat/route.ts` y `src/app/api/manager/[...path]/route.ts` **duplican** el bloque de configuración de API_URL/API_KEY/CF_CLIENT_ID/CF_CLIENT_SECRET en vez de usar `apiFetch`. Cada uno tiene su justificación (maxDuration 300 s para chat, multipart para manager/intel, passthrough de status para ambos), pero las primeras ~20 líneas son idénticas.

**Recomendación**: extraer un helper `apiFetchRaw(path, init)` en `src/lib/api.ts` que devuelva la `Response` cruda. Los dos casos especiales lo usan y el bloque de setup queda en un solo lugar.

### C3 — `pause-banner.tsx` polling cada 30 s

El banner que muestra el estado del cluster Atlas (pause/resume) hace polling cada 30 s. Si en algún momento el backend expone `/api/health/deep` con el estado del cluster, este polling se puede eliminar o reducir a eventos de foco.

**Recomendación**: sin cambio inmediato. Flag para cuando se implemente health deep del backend.

---

## 4. Lo que sí está bien ✅

Para que el reporte no sea sólo crítica:

- **Zero leaks de secretos** al bundle cliente. Todos los `process.env.API_KEY` / `CF_ACCESS_CLIENT_SECRET` / `MANAGER_EMAILS` viven en archivos server (route handlers, `lib/api.ts`, `layout.tsx`, `proxy.ts`).
- **Separation of concerns correcto**: pages son async server components; componentes con interactividad están marcados `"use client"` sin abusar.
- **Headers de auth propagados correctamente** en todas las proxy routes (Bearer + CF service token).
- **`apiFetch` tiene timeout default (15 s)** y handler especial de `AbortError`, evitando cuelgues indefinidos.
- **`/api/chat` y `/api/manager/intel/extract`** implementan proxy custom con buen motivo (maxDuration, multipart, passthrough de status tipado) — la duplicación vs `apiFetch` es consciente y comentada.
- **El `proxy.ts`** usa `MANAGER_EMAILS` sincronizado con el backend (mismo nombre de env var, misma semántica). Fail-open en dev, fail-closed en prod.
- **safeFetch pattern** en pages: cada fetch tiene fallback, si el backend cae la página rendea con datos vacíos en vez de cuartarse.

---

## 5. Plan de acción priorizado

Si querés aplicar los fixes, yo los agruparía así:

**Sprint 1 (30 min, altísimo ROI)**:

1. Sacar `<AutoRefresh intervalMs={5000} />` de `layout.tsx`. Smoke test en local + preview de Vercel → confirmar que las vistas live siguen andando por su polling propio.
2. (Opcional) Agregar `<AutoRefresh intervalMs={30000} />` SOLO en `/renta-fija/page.tsx` y `/derivados/page.tsx` si necesitás live en esas páginas.

**Sprint 2 (1-2 h)**:

3. Revisar TTLs de las 10 rutas proxy con `revalidate: 0` innecesario. Alinearlos al ciclo real del dato (ver tabla §B1).
4. Normalizar `Cache-Control` header (§B2).

**Sprint 3 (opcional, 30 min)**:

5. Agregar `"/api/manager/:path*"` al matcher de `proxy.ts` (§B3).
6. Extraer helper `apiFetchRaw` en `lib/api.ts` y aplicar a `api/chat/route.ts` + `api/manager/[...path]/route.ts` (§C2).

**Follow-up**:

7. Documentar o eliminar endpoints backend sin consumidor (§C1). Requiere decisión producto.

---

## 6. Verificación

Una vez aplicados los fixes del Sprint 1:

1. En la consola de Vercel → Functions, ver la caída en invocations por minuto.
2. En el backend, `journalctl -u api.service -f` → debería ver bloques de silencio entre requests en vez de stream continuo.
3. Cloudflare → Security → Events: ya no debería aparecer tráfico marcado como bot (fue lo que disparó el problema del 502 de hoy).
4. Manager → Recursos: la CPU/RAM del proceso api deberían bajar.

---

*Auditor: Claude (Opus 4.7). Este documento es un reporte de estado, no un design doc. Las recomendaciones son sugerencias — la decisión de aplicarlas y en qué orden es del equipo.*
