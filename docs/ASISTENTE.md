# ACAQuant — Asistente de Mesa (IA Generativa)

Documentación técnica completa del asistente conversacional integrado en
acaquant-web. Esta doc es **la fuente de verdad** del módulo: se mantiene
viva a medida que agregamos features.

---

## 1. Propósito

**Qué es**: un asistente conversacional **estratégico** (no un dashboard más)
integrado en `trading.acaquant.com/asistente`. Su valor está en conectar
puntos entre datos de mercado y la operativa de la mesa — propone tesis con
fundamentos numéricos, aplica el framework analítico de la firma, responde
en el tono y vocabulario de la mesa argentina.

**Qué NO es**: un lookup. El usuario ya tiene las tablas de cotizaciones,
curvas, forwards y breakevens en `/renta-fija`. El asistente se usa cuando
la pregunta requiere **análisis**, **comparación** o **recomendación
direccional**.

**Alcance de datos (actual)**: data pública de mercado — cotizaciones,
series BCRA, forwards, breakevens, opciones GGAL, metadata de bonos. **Sin
acceso** a carteras, AuM, operaciones ni contrapartes. Bloqueado por
política de datos mientras usemos free tier de LLM. Con billing Claude
activo se puede destrabar.

---

## 2. Arquitectura

```
Usuario
  │
  ▼
trading.acaquant.com/asistente  ← componente <ChatView /> (acaquant-web)
  │
  │ POST /api/chat {message, history}
  ▼
acaquant-web proxy (src/app/api/chat/route.ts)  ← cf-access-authenticated-user-email
  │
  │ Bearer API_KEY + CF service token
  ▼
api.acaquant.com → CF Tunnel → FastAPI
  │
  ▼
api/routers/chat.py
  │
  │ run_conversation(user_message, history)
  ▼
api/agent/runner.py
  │
  ├─ api/agent/router.py      → decide_model(user_message) → 'haiku' | 'sonnet'
  ├─ api/agent/provider.py    → get_provider(alias) → ClaudeProvider | GeminiProvider
  ├─ api/agent/context.py     → build_market_context() (MEP, CER, top volumen, vtos, INTEL)
  ├─ api/agent/data_inventory → build_data_inventory() (qué colecciones hay, rango)
  ├─ api/agent/prompt.py      → build_system_prompt(mkt_ctx, data_inv)
  └─ api/agent/tools.py       → TOOLS[] + dispatch()
  │
  │  tool-use loop hasta MAX_STEPS=6 o respuesta final
  ▼
[Claude Messages API (https://api.anthropic.com/v1/messages)]
```

### 2.1 Abstracción provider-agnostic

El runner trabaja con **mensajes en formato canónico** (estilo Claude). Cada
provider (`ClaudeProvider`, `GeminiProvider`) implementa la misma interfaz:

```python
class LLMProvider(ABC):
    def generate(
        self,
        messages: list[dict],   # formato canónico
        system_prompt: str,
        tools: list[dict],       # formato interno (TOOLS)
        temperature: float,
    ) -> LLMResponse: ...
```

`LLMResponse` tiene `text`, `tool_calls`, `usage`, `stop_reason`,
`assistant_message`.

Para cambiar de provider: `LLM_PROVIDER=claude|gemini` en `.env`. Por
default es `claude`. Gemini queda como fallback legacy.

### 2.2 Router Haiku/Sonnet (solo Claude)

`api/agent/router.py::decide_model(user_message)` clasifica cada mensaje con
heurísticas por keywords:

- **Sonnet** (razonamiento fuerte): detecta comparaciones (`CER o Lecap`,
  `vs`), recomendaciones (`recomendá`, `qué opinás`, `cómo ves`),
  estructuras técnicas (`barbell`, `butterfly`, `covered call`, `steepener`,
  `TIPS-Treasury`), framework (`régimen`, `valor relativo`, `riesgo
  político`), brechas/licitación.
- **Haiku** (lookup rápido): todo lo demás, saludos, cotizaciones puntuales,
  metadata.
- **Mensajes > 280 chars** → Sonnet por default (probablemente elaborado).

Override opcional via parámetro `force_model` en `run_conversation`.

### 2.3 Prompt caching (Claude)

El system prompt se envía con `cache_control: ephemeral` → Claude cachea el
prefijo por 5 minutos. A partir del segundo mensaje en esa ventana, el
input cacheado cuesta 10% del normal (~$0.30/M vs $3.00/M).

Para conversaciones activas (ventana de 5 min) esto reduce el costo total
aproximadamente un 40-60%.

---

## 3. Componentes

### 3.1 Backend — `api/agent/`

| Archivo | Rol |
|---|---|
| `__init__.py` | — |
| `types.py` | `LLMResponse`, `ToolCallRequest`, helpers `user_text_message()` y `tool_result_message()`. Formato canónico estilo Claude. |
| `provider.py` | `LLMProvider` ABC + `ClaudeProvider` (Messages API + caching) + `GeminiProvider` (legacy) + factory `get_provider()`. |
| `router.py` | `decide_model(user_message)` → `'haiku'` o `'sonnet'` por heurísticas. |
| `runner.py` | Bucle tool-use. Convierte user_message + history en conversación; invoca provider; dispatcha tools; itera hasta `MAX_STEPS=6`. |
| `prompt.py` | `SYSTEM_PROMPT_BASE` (~1.5K tokens, compacto) + `build_system_prompt(mkt, data_inv)` que compone prompt final. |
| `context.py` | `build_market_context()` arma la "foto del día": fecha/hora AR, estado de rueda, MEP, CER, A3500, top volumen, próx. vtos Lecap/CER, breakevens, últimas emisiones. Suma bloque `INTEL MÁS RECIENTE` si hay IntelDoc confirmado. Cache 60s. |
| `data_inventory.py` | `build_data_inventory()` introspecta Mongo: qué colecciones existen, rango de fechas, doc count. Cache 5 min. Incluye lista de "lo que NO hay" (riesgo país, licitaciones, etc). |
| `estrategia.py` | Loader con mtime-cache de `docs/asistente/estrategia.md`. Retornado por la tool `consultar_framework_analitico()`. |
| `estrategias.py` | Loader + slicer de `docs/asistente/estrategias.md` por 14 secciones. Retornado por la tool `consultar_catalogo_estrategias(tema)`. |
| `intel_extraction.py` | Schema JSON + función `extract_intel(text)` que llama a Gemini Flash con `responseSchema` para pulls out variables macro estructuradas. |
| `tools.py` | Lista `TOOLS[]` (~25: datos live + analítica Tier 1 + Tier 2 + locales), `dispatch(name, args)` que ejecuta, `_is_blocked()` que impone policy de datos (bloquea `/api/portfolio/*`, `/api/operaciones/*`, `/api/cuentas/*`, `/api/manager/*`). |
| `service_registry.py` | Mapping endpoint → función Python directa. Cuando hay handler registrado, `dispatch()` lo llama sin HTTP loopback (~100-300 ms menos). |
| `invariants.py` | Checks de dominio sobre tool outputs (paridad ∈ [0,150], duration ≥ 0, convexity ≥ 0, residuales monotónicos, amortizaciones ≈ 100). Warnings se anexan a `_meta.warnings`. |
| `tool_metadata.py` | Calcula staleness (fresh/stale/very_stale/unknown) a partir de timestamps en el payload. |
| `ticker_catalog.py` | `did_you_mean()` — sugerencias cuando un ticker no se resuelve. |

### 3.2 Backend — endpoints

| Endpoint | Método | Propósito |
|---|---|---|
| `/api/chat` | POST | Turno de conversación. Body: `{message, history}`. |
| `/api/manager/asistente/stats` | GET | Métricas agregadas (conversaciones, tokens, latencia, costo). Query: `horas`. |
| `/api/manager/asistente/logs` | GET | Últimas N conversaciones. Query: `limit`, `estado`, `horas`. |
| `/api/manager/asistente/timeseries` | GET | Buckets por hora: count + tokens + errors. |
| `/api/manager/asistente/tools-ranking` | GET | Ranking de tools invocadas con ok/fail. |
| `/api/manager/intel/extract` | POST | Multipart (texto y/o PDF) → extrae variables con Gemini, devuelve preview sin persistir. |
| `/api/manager/intel/save` | POST | Persiste IntelDoc confirmado. |
| `/api/manager/intel` | GET | Lista IntelDocs ordenados desc por fecha. |
| `/api/manager/intel/latest` | GET | Último IntelDoc confirmado (usado internamente por `context.py`). |
| `/api/manager/intel/{id}` | GET / PATCH / DELETE | CRUD sobre IntelDoc individual. |

### 3.3 Frontend — `acaquant-web/src/components/`

| Archivo | Rol |
|---|---|
| `chat-view.tsx` | Vista principal del chat en `/asistente`. Streaming de turnos, auto-scroll, panel de errores amigable, expansión de "Fuentes" (tool calls), meta (steps/elapsed/tokens/modelo), renderer inline de markdown (`[texto](url)`, `**bold**`, `` `code` ``). |
| `asistente-dashboard.tsx` | Tab **ASISTENTE** en `/manager`. Panel superior de 6 métricas, charts de bar (conv/hora) + line (tokens/hora) + ranking de tools, tabla navegable con filtros por período y estado, expand para ver pregunta/respuesta/tool_calls completos. Auto-refresh 10s. |
| `intel-panel.tsx` | Tab **INTEL** en `/manager`. Uploader (paste texto o PDF drag+drop), preview editable con las 12 variables + comentario macro, tabla de historial con expand + delete. |
| `manager-view.tsx` | Tabs: DIAGNÓSTICO · BACKFILLS · VALIDACIONES · HISTORIAL · LATENCIA · **ASISTENTE** · **INTEL**. Gated por `MANAGER_EMAILS`. |

### 3.4 Frontend — proxies y rutas

| Ruta | Rol |
|---|---|
| `src/app/asistente/page.tsx` | Monta `<ChatView />`. |
| `src/app/api/chat/route.ts` | Proxy POST a `/api/chat` (maxDuration 300s). |
| `src/app/api/manager/[...path]/route.ts` | Proxy general a `/api/manager/*` con soporte GET/POST/PATCH/DELETE y multipart (para `intel/extract` con PDF). maxDuration 90s. |
| `src/proxy.ts` | Gating por `MANAGER_EMAILS` (Next 16, antes llamado middleware). Cubre `/manager`, `/asistente`, `/api/chat`. |
| `src/app/layout.tsx` | Computa `isManager` vía `cf-access-authenticated-user-email` y lo pasa al `<Header />`. |

### 3.5 Archivos de contenido editables

| Archivo | Rol |
|---|---|
| `docs/asistente/estrategia.md` | ADN analítico de la mesa: framework de 4 capas, house view, señales gatillo, rotaciones típicas, umbrales operativos, vocabulario. Editable sin tocar código. Releído al cambiar mtime. |
| `docs/asistente/estrategias.md` | Catálogo técnico de ~45 estrategias (FI, opciones GGAL, FX, inflation, macro) con fórmulas, datos necesarios y construcción paso a paso. Sliceado por 14 temas. |

---

## 4. Persistencia — colecciones Mongo

| Colección | Qué guarda | Uso |
|---|---|---|
| `Manager.AsistenteLogs` | Cada turno del chat: `ts`, `user`, `message`, `reply`, `tool_calls`, `usage`, `steps`, `elapsed_s`, `truncated`, `estado`, `error`, `model_used`, `history_len` | Dashboard Manager/ASISTENTE; auditoría. |
| `Manager.IntelDocs` | Reportes cargados: `fuente`, `fecha`, `titulo`, `raw_text`, `extracted` (12 vars), `confirmed`, timestamps | Context inyecta el más reciente; tab Manager/INTEL lista/edita/borra. |
| `Manager.AsistenteFeedback` *(planificada)* | 👍/👎 por turno | Métrica de calidad; backlog de mejora. |

---

## 5. Variables de entorno

```
# LLM — en .env del backend
ANTHROPIC_API_KEY=sk-ant-api03-...  # obligatoria si LLM_PROVIDER=claude
GEMINI_API_KEY=AIzaSy...            # obligatoria si LLM_PROVIDER=gemini (y para intel_extraction siempre)
LLM_PROVIDER=claude                  # claude (default) | gemini
API_KEY=...                          # Bearer para /api/chat y todo el router

# Frontend — en .env.local de acaquant-web
API_URL=https://api.acaquant.com
API_KEY=...                          # mismo que el backend
CF_ACCESS_CLIENT_ID=...              # service token para bypass CF Access
CF_ACCESS_CLIENT_SECRET=...
MANAGER_EMAILS=admin1@acaquant.com,admin2@... # gating de /manager, /asistente, /api/chat
```

---

## 6. Políticas de datos y seguridad

- **Gating admin**: `/asistente` y `/manager` están detrás de `MANAGER_EMAILS`.
  `src/proxy.ts` valida el email del header `cf-access-authenticated-user-email`
  y redirige a `/` si no matchea.
- **Bloqueo Grupo 3**: las tools del asistente tienen prefijos prohibidos
  (`/api/portfolio`, `/api/operaciones`, `/api/cuentas`, `/api/manager`) que
  `_is_blocked()` filtra tanto al declarar las tools para el modelo como al
  hacer dispatch. Doble cinturón.
- **Free tier Gemini entrenaba con la data** → por eso el Grupo 3 estaba
  bloqueado. Con billing Claude (default actual), Anthropic no entrena con
  la data por defecto + ZDR opcional → se puede destrabar.
- **Auditoría**: cada turno queda en `Manager.AsistenteLogs` (incluyendo
  errores con `estado=error` y el código de falla). Visible en tiempo real
  en el tab **ASISTENTE** del Manager.

---

## 7. Costo operativo

**Provider actual**: Claude con router automático Haiku/Sonnet.

**Pricing** (paid tier, USD por millón de tokens):
- Haiku 4.5: `$1.00/M input` · `$5.00/M output` · `$0.10/M cached read`
- Sonnet 4.6: `$3.00/M input` · `$15.00/M output` · `$0.30/M cached read`

**Estimación con mix 70% simple / 30% estratégico + cache hit ~50-70%**:

| Escenario | Mensajes/mes | Costo Claude aprox |
|---|---|---|
| Solo admin testing | 330 | USD 10-15 |
| 2-3 PMs activos | 1.000 | USD 25-35 |
| Mesa chica (3-4 PMs) | 1.600 | USD 40-50 |
| Mesa completa (5+ PMs) | 2.200 | USD 55-80 |

**Spending limit recomendado**: USD 50-100/mes en
`console.anthropic.com → Billing → Budgets & alerts`.

---

## 8. Observabilidad

### 8.1 Tab ASISTENTE en Manager

- **Top cards** (period-filterable: 1h/6h/24h/7d/30d):
  - Conversaciones (OK/err split)
  - Tasa de éxito (coloreada)
  - Tokens totales (in/out breakdown)
  - Latencia avg + p95
  - Costo estimado USD

- **Charts**:
  - Bar: conversaciones por hora (anaranjado; se colorea si hubo errores)
  - Line: tokens por hora
  - Ranking horizontal de tools usadas con contador de fallos

- **Tabla**:
  - Columnas: hora · usuario · pregunta · steps · elapsed · tokens · estado badge.
  - Click expande → ver pregunta, reply, tool_calls con args y resultados, error detallado, metadata.
  - Filtros: período, estado (all/ok/error/truncated).
  - Auto-refresh cada 10s.

### 8.2 Errores tipados

El backend clasifica los errores LLM en 5 códigos:

| Code | Status | Retryable | Ejemplo |
|---|---|---|---|
| `rate_limit` | 429 | ✓ (60s) | Gemini free TPM tocado |
| `transport` | 503 | ✓ (10s) | DNS/TLS/red rota |
| `bad_response` | 502 | ✓ | Modelo devolvió payload malformado |
| `llm_error` | 502 | ✓ | Error genérico del provider |
| `internal` | 500 | ✗ | Bug interno |

El frontend parsea el payload estructurado y muestra UI amigable con
ícono, título, hint y botón Reintentar.

---

## 9. Funcionalidades — estado actual (2026-04-21)

### ✅ Implementado

- [x] Provider Claude con router Haiku/Sonnet automático + prompt caching.
- [x] Fallback Gemini via `LLM_PROVIDER=gemini`.
- [x] Framework de 4 capas editable (`estrategia.md`) + catálogo ~45 estrategias (`estrategias.md`).
- [x] Contexto dinámico (market + data inventory + último IntelDoc).
- [x] Tool-use con ~25 tools (live + analítica Tier 1 + Tier 2 + locales).
- [x] Bloqueo Grupo 3 (`/api/portfolio/*`, `/api/operaciones/*`, `/api/cuentas/*`, `/api/manager/*`) con doble cinturón (declaración + dispatch).
- [x] Errores tipados + UI amigable con retry.
- [x] Render inline de markdown en respuestas.
- [x] Dashboard Manager/ASISTENTE con métricas, charts, logs, tools-ranking.
- [x] Tab Manager/INTEL: carga PDFs + extracción estructurada + inyección al contexto.
- [x] Service registry sin HTTP loopback (dispatch llama funciones Python directo).
- [x] Invariantes de dominio sobre tool outputs (paridad, convexity, duration, amortizaciones).
- [x] Staleness + `did_you_mean` metadata en cada tool result.
- [x] Serialización segura de datetime en `tool_result_message` (no rompe turnos ante fechas crudas de Mongo).

### 🟢 Tools de data (última actualización 2026-04-21)

**Cotizaciones live**:
- `cotizacion_renta_fija`, `cotizacion_opciones`, `forwards_por_curva`, `breakevens_actuales`, `mep_actual`
- `caucion_actual` — TNA ARS/USD del plazo dinámico (1D/3D)
- `caucion_historica` — cierre histórico por moneda
- `futuros_dlr` — curva entera outrights con TNA implícita
- `futuros_dlr_historico` — cierre histórico por ticker
- `argy_overview` — MEP + CCL + canje + caución ARS/USD con returns
- Series BCRA: `serie_cer`, `serie_badlar`, `serie_dolar_a3500`
- Históricos: `historico_trades`, `historico_forwards`, `historico_breakevens`, `historico_mep`, `historico_curva`

**Metadata y equity**:
- `metadata_activos`, `flujos_titulo`, `cotizacion_equity`, `calendario_economico`

**Analítica Tier 1** (benchmarks dinámicos):
- `listar_curva` — curva entera enriquecida (incluye `convexity`)
- `obtener_serie_macro` — serie + stats + clasificación. Variables soportadas: tamar, cer, dolar, badlar, mep, ccl, canje, caucion_ars, caucion_usd, `<TICKER>.<CAMPO>`. Bloqueadas por falta de data: ipc, ipim, riesgo_pais, repo, rem_inflacion.
- `clasificar_nivel` — wrapper compacto (solo etiqueta + percentil).

**Analítica Tier 2** (sobre data existente):
- `snapshot_curva_historico(curva, fecha)` — curva entera a fecha pasada
- `pendiente_curva(curva, metrica?, fecha_comparacion?)` — slope en bps + delta vs pasado
- `liquidez_secundario(ticker, dias?)` — ratio vs promedio + clasificación

**Tools locales** (on-demand, no HTTP):
- `consultar_framework_analitico` — carga `docs/asistente/estrategia.md`
- `consultar_catalogo_estrategias(tema)` — carga sección de `docs/asistente/estrategias.md`

### 🟡 Pendiente (roadmap)

1. **BYMA Primarias Placements** — cliente OAuth2 (`core/byma.py`) + smoke test listos. Esperando credenciales/scope en el portal BYMA. Desbloquea tools de licitaciones históricas, issuers, underwriters, docs de colocación.
2. **Feedback 👍/👎** por respuesta → `Manager.AsistenteFeedback` → input para mejorar prompt.
3. **Suite de evals** contra `golden_set.yaml` (55 test cases ya definidos) para regression testing.
4. **RAG con embeddings** sobre `IntelDocs` con Atlas Vector Search. Tool `buscar_reportes(query)`. Hoy solo se inyecta el último IntelDoc confirmado.
5. **Email forwarding** a `intel@acaquant.com` → ingesta automática de reportes.
6. **Chips de sugerencias curadas** en empty state del chat.
7. **Exportar conversación** a PDF/markdown.
8. **Gráficos inline** (modelo devuelve JSON → frontend renderiza Lightweight Chart).
9. **"Modo análisis profundo"** con Opus 4.7 (botón explícito, cost-marked).
10. **Streaming UX** (SSE) — mostrar "pensando..." y tool calls en vivo en lugar de full-block al final.
11. **Destrabar Grupo 3** (carteras/AuM/operaciones) con Claude + ZDR. Policy con compliance + activar ZDR Anthropic.
12. **Data macro faltante**: IPC/IPIM INDEC (scraping mensual), riesgo país EMBI+ (scraping ámbito), REM BCRA (CSV mensual), Hard Dollar enrichment (YTM/duration/convexity para Globales), Dólar Linked seedeado.
13. **Multi-tenant** si se vende a otra ALYC.

---

## 10. Cómo extender

### Agregar una tool nueva
Editar `api/agent/tools.py`, agregar un dict a `TOOLS[]`:
```python
{
    "name": "mi_tool",
    "description": "Descripción corta (aparece en el menú del modelo).",
    "endpoint": "/api/cotizaciones/mi-endpoint",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "param_name": {"type": "STRING", "description": "..."},
        },
        "required": ["param_name"],
    },
},
```

Si la tool es **local** (no HTTP): `endpoint = "__local__:mi_handler"` y
agregar el branch correspondiente en `dispatch()`.

### Editar el framework analítico
`docs/asistente/estrategia.md` — texto plano. Se releé automáticamente.

### Agregar una estrategia al catálogo
`docs/asistente/estrategias.md` — seguir el template: outlook, estructura,
datos necesarios, fórmulas, construcción paso a paso, caveats. Si agregás
una sección nueva, mapearla en `SECCIONES` de `api/agent/estrategias.py`.

### Cambiar de provider (ej: Opus)
`LLM_PROVIDER=claude` (ya) + en `router.py` agregar regla que devuelva
`'opus'` para ciertos triggers. `provider.py::get_provider()` ya soporta
`opus` como alias.

### Agregar variable al schema de INTEL
Editar `api/agent/intel_extraction.py::EXTRACTION_SCHEMA` + `FIELD_LABELS` +
el campo en `intel-panel.tsx` (`FIELDS[]`) + `context.py::_INTEL_LABELS`.

---

## 11. Scripts de diagnóstico

```bash
# Smoke test endpoint /api/chat (requiere uvicorn corriendo)
python -m scripts.test_chat "hola"
python -m scripts.test_chat "cómo ves el mercado"

# Smoke test Gemini API (ambos modelos)
python -m scripts.test_gemini

# Diagnóstico completo ANTHROPIC_API_KEY (formato, caracteres raros, llamada real)
python -m scripts.debug_claude_key
```

---

## 12. Troubleshooting

| Síntoma | Causa probable | Fix |
|---|---|---|
| `503 ANTHROPIC_API_KEY no configurada` | falta la env var | editar `.env` + `systemctl restart api.service` |
| `429 rate limit Claude` | pasaste RPM/TPM; rare en paid | reintentar en 60s |
| `429 rate limit Gemini — cuota: RPD` | free tier consumido del día | esperar o pasar a Claude |
| `502 bad_response 401 invalid x-api-key` | key mal copiada | `python -m scripts.debug_claude_key` |
| UI timeout | CF tunnel o Vercel tardó demasiado | revisar `systemctl status api.service`; probar con `test_chat` directo |
| Respuestas demasiado largas | el modelo ignoró la regla anti-dump | verificar que el prompt tenga las reglas últimas; reiniciar service |
| Conversación vieja no responde | history en formato Gemini tras migrar a Claude | ignora automáticamente; clickeá "Nueva conversación" |

---

## 13. Changelog breve

- **2026-04-19** — V1 con Gemini Flash + catálogo + framework (prompt gordo).
- **2026-04-19** — Catálogo y framework movidos a tools on-demand (90% menos tokens en simples).
- **2026-04-19** — Sistema de errores tipados + UI amigable con retry.
- **2026-04-19** — Dashboard Manager/ASISTENTE con métricas live.
- **2026-04-19** — Tab Manager/INTEL: carga de reportes con extracción estructurada + inyección al contexto.
- **2026-04-19** — Switch a Claude con router Haiku/Sonnet + prompt caching.
- **2026-04-19** — Regla anti-dump de tablas + linkeo a vistas UI.
- **2026-04-20** — Tier 2 audit: service layer (`api/services/*`) sin FastAPI, dispatch directo sin HTTP loopback.
- **2026-04-20** — Tools Tier 1 expuestas en `/api/analitica/*`: `listar_curva`, `obtener_serie_macro`, `clasificar_nivel`.
- **2026-04-21** — CCL + canje live via WS (`engines/dolares.py`), disponibles como variables macro (`ccl`, `canje`).
- **2026-04-21** — Tool `caucion_actual` + `caucion_historica` (plazo dinámico 1D/3D según próximo hábil).
- **2026-04-21** — Tool `futuros_dlr` (curva outrights con TNA implícita) + `futuros_dlr_historico`.
- **2026-04-21** — Tool `argy_overview` (panel MEP/CCL/canje/caución con returns %Día/%7d/%MTD/%YTD).
- **2026-04-21** — `convexity` agregado al enriquecimiento de `Trading.TimeSales` y expuesto en `listar_curva`.
- **2026-04-21** — Tools Tier 2: `snapshot_curva_historico`, `pendiente_curva`, `liquidez_secundario`.
- **2026-04-21** — Fix serialización datetime en `tool_result_message` (rompía turnos con fechas crudas).
- **2026-04-21** — Scaffolding cliente BYMA Primarias Placements (`core/byma.py`) pendiente desbloqueo portal.
