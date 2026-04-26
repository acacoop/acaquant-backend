# Flows estructurados (structured intake)

Patrón para tareas frecuentes con input conocido (cartera, análisis bono,
comparar curvas). En lugar de chat libre, el frontend muestra un formulario
tipado, el backend construye un user message determinístico y fuerza al
modelo a responder vía una tool con schema fijo.

Convive con el chat libre — no lo reemplaza. Chat libre = exploratorio /
no-frecuente. Flow estructurado = tarea repetitiva con shape conocido.

## Por qué este patrón

Tres ganancias vs lenguaje natural:

1. **Input predecible**. El modelo recibe `{perfil: agresivo, plazo: largo, ...}`
   en vez de "armame cartera agresiva onda inflación pesos". No hay que parsear,
   no se olvida campos.
2. **Output comparable**. Dos consultas con mismos params dan respuestas
   estructuralmente idénticas. Permite eval objetivo (golden set).
3. **UI determinística**. El frontend renderiza con un componente dedicado
   por flow — tabla de instrumentos, expandibles, no markdown libre.

## Anatomía de un flow

Cada flow vive en su propio módulo bajo `api/agent/structured/`. Por
ejemplo `cartera.py` define todo lo de "Recomendar cartera":

```
api/agent/structured/cartera.py
├── CarteraRequest (Pydantic)         # schema de input del formulario
├── RESPONDER_CARTERA_TOOL (dict)     # tool de output (forzada via tool_choice)
├── SYSTEM_PROMPT_ADDENDUM (str)      # reglas extra que se inyectan al system prompt
├── build_user_message(req) -> str    # mapeo determinístico schema → texto
├── validar_pesos_suman_100(...)      # validación post-output
└── run_cartera_flow(req) -> dict     # orquesta todo (build + runner + validar)
```

El runner genérico (`api/agent/runner.py`) recibe parámetros nuevos para
soportar cualquier flow estructurado:

- `extra_tools`: tools adicionales que se PASAN al modelo pero NO se
  dispatchan (sus args son output estructurado).
- `force_tool_name_on_last`: nombre de la tool a forzar via `tool_choice`
  en el último step disponible. Garantiza output estructurado aunque el
  modelo no quiera solo.
- `extra_system_blocks`: bloques adicionales al system prompt (sin
  cache_control típicamente).

Cuando el modelo llama una `extra_tool`, el runner intercepta y devuelve
los args como `result["structured_output"]`.

## Cómo agregar un flow nuevo

1. Crear `api/agent/structured/<nombre>.py` con los 6 elementos de la
   anatomía (modelo Pydantic, tool, addendum, build_user_message,
   validador opcional, función `run_<nombre>_flow`).

2. Re-exportar en `api/agent/structured/__init__.py`.

3. Agregar endpoint dedicado en `api/routers/chat.py`:
   `POST /api/chat/structured/<nombre>` con response model dedicado.

4. Logging: usar `tipo: "structured_<nombre>"` en `Manager.AsistenteLogs`
   con los params del request como `metadata`.

5. Frontend (`acaquant-web`): proxy en
   `src/app/api/chat/structured/<nombre>/route.ts`, componente de form
   `<NombreForm />` y componente de respuesta `<NombreResponse />`,
   integrar al `chat-view.tsx` existente con un chip de acción rápida.

6. Eval: agregar caso(s) a `docs/asistente/golden_set_<nombre>.yaml` y
   adaptar `scripts/eval_<nombre>.py`.

## Reglas

- El user message es 100% generado por código a partir del schema
  validado. **NO concatenar texto libre del usuario** — eso rompe la
  garantía de input predecible.

- El modelo debe llamar la tool de output. Si truncated o si el modelo
  no la llamó, devolver `data: null` + `error` al frontend con mensaje
  útil.

- Las tools de output (`RESPONDER_*_TOOL`) NO van al array `TOOLS`
  global de `tools.py`. Se pasan vía `extra_tools` solo en su flow.
  Razón: si estuvieran en TOOLS, el chat libre podría invocarlas.

- Evals con criterios booleanos sobre estructura, no sobre contenido
  exacto. Los tickers cambian día a día, los pesos también — lo que se
  valida es que sume 100, que tenga 3-5 instrumentos, que llame la tool.

## Flow vigente

- **cartera** (`cartera.py` → `POST /api/chat/structured/cartera`)
  Recomienda cartera por perfil/exposición/plazo/benchmark.

## Próximos (TODO)

- `analisis_bono`: análisis profundo de un instrumento (precio, métricas,
  comparables, riesgos). Input: ticker + opcionalmente horizonte.
- `comparar_curvas`: comparar dos curvas (CER vs tasa fija, etc) en un
  rango de duration.
