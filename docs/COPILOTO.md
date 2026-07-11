# Copiloto de Mesa — doc vivo (QuantAI P3)

> **REGLA DE ESTE DOC:** todo cambio que modifique lo que el asistente VE
> (columnas, bloques, reglas del prompt, vistas) o CÓMO se comporta, se asienta
> en el **Changelog** de abajo CON FECHA, en el MISMO commit del cambio. Si el
> changelog no refleja lo que corre en prod, el cambio está incompleto.
> Roadmap y decisiones de programa: `docs/QUANTAI.md` (P3). Acá vive el detalle
> operativo del asistente.

## Dónde vive el código (mapa)

| Pieza | Archivo | Qué hace |
|---|---|---|
| **El cerebro** | `api/services/copiloto.py` | TODO el comportamiento: registro `VISTAS` (qué datos, columnas, reglas), system prompt, detección de tickers, bloques de detalle, serialización TSV, feedback. **Tocar el asistente = tocar este archivo.** |
| Transporte LLM | `core/ai.py` | Gateway único: tarea `copiloto_vista` (tier flash, max_tokens 2000), presupuesto diario, retry, traza de cada llamada. No sabe nada del copiloto. |
| HTTP | `api/routers/ia.py` | 3 endpoints: `GET /api/ia/copiloto/vistas`, `POST /api/ia/copiloto`, `POST /api/ia/copiloto/feedback`. Solo plumbing; gate `ia` en el montaje. |
| Panel UI | `acaquant-web/src/components/ia-vista-panel.tsx` | Drawer + botón "Consultale a la IA". Historial corto client-side. Se oculta si el backend no habilita la vista. |
| Ubicación del botón | `acaquant-web/src/components/renta-variable-shell.tsx` | Monta `<IaVistaPanel vista="renta_variable" />` en la barra de tabs. |
| Observabilidad | tabla `ia.trazas` + Manager → OBSERVABILIDAD → pill IA | Cada pregunta: tokens, latencia, ok/error, feedback 👍/👎. |
| Tests | `tests/unit/test_copiloto.py` | Congelan contrato: TSV, gates, caps, degradación, detección de tickers. |

## Cómo fluye una pregunta

```
Panel (browser) ── {vista, pregunta, historial} ──► POST /api/ia/copiloto
    (los DATOS nunca viajan del browser)                │
                                                        ▼
   copiloto.preguntar():  fetch del service @cached de la vista
                          → enriquecer (columnas derivadas)
                          → TSV + extras (CCL, detalle por ticker mencionado)
                          → core.ai.completar_con_traza("copiloto_vista")
                          → {respuesta, traza_id}  (ok=False si algo falla)
```

## Qué ve el asistente HOY (vista `renta_variable`)

- **Tabla completa** (~187 CEDEARs × 26 columnas): identificación (ticker,
  nombre, underlying, ratio, sector, rubro, país, **es_ia**), precios ARS,
  variaciones (intradía, 1d, 1d USD), liquidez (bid/offer/spread/volumen/
  monto), subyacente USD (precio, variaciones, retornos WTD/7d/MTD/YTD,
  volumen USD) y **zonas de pivots** `piv_anual` / `piv_mensual`.
- **CCL live** (siempre, 1 línea).
- **Detalle por ticker MENCIONADO en la pregunta** (cap 3, detección
  determinista de tokens): pivots 4 marcos con niveles, quant (beta/corr vs
  SPY/QQQ, vol, z-score), últimos 15 retornos diarios, fundamentals Refinitiv
  (si la empresa está en `research.companies`).
- **Conocimiento de mesa en el prompt:** significado de cada columna + lectura
  de pivots (>R3/<S3 = movió muchísimo · R2/S2 = tendencia clara · R1/S1 =
  movió algo · PP = referencia ideal de decisión) + manejo de pedidos de
  recomendación (ranking objetivo con criterio, nunca consejo de inversión).

## Cómo se agrega una vista nueva

1. Entrada en `VISTAS` (copiloto.py): `titulo`, `modulo` RBAC, `fetch` (el
   MISMO service @cached de la vista), `columnas`, `reglas` del dominio, y
   opcionales `enriquecer` / `extras`.
2. `<IaVistaPanel vista="..." />` donde la vista lo quiera (el padre posiciona).
3. Entrada acá en el changelog + estado en QUANTAI.md.

---

## Cómo se evalúa (candado de regresión)

**Decisión del user (2026-07-11): las pruebas las hace ÉL, manualmente, desde
el panel** — preguntas reales + 👍/👎. No pedirle presupuesto para el runner
ni proponer correrlo de rutina.

`evals/copiloto_vista.json` (casos reales del shadow + adversos) + runner
`python -m scripts.eval_copiloto` quedan como herramienta OPCIONAL para
momentos puntuales (ej. antes de abrir el copiloto al resto de la mesa).
Cada fallo real del shadow se sigue agregando como caso — documenta qué NO
puede volver a romperse, se corra o no el runner.

## Plan "prompt engineering para finanzas" (guía AI-in-Finance, 2026-07-11)

Adopciones decididas charlando con el user (TODO se implementa CON su ayuda y
queda asentado acá):
1. **Audiencia por rol RBAC** — **HECHO (2026-07-11, tono definido por el
   user):** trader = seco/numérico, sales = explicado y con frases repetibles
   a un cliente, admin = neutro. `_TONO_POR_ROL` en copiloto.py vía
   `get_user_role` (best-effort: roles caídos → neutro).
2. **Biblioteca de consultas de mesa** — **HECHO (2026-07-11, chips elegidos
   por el user):** 5 chips en el panel (Papeles de IA · Argentina · En zona
   de decisión · Rezagados repuntando · Voladores del año), prompts curados
   en `_CHIPS_RENTA_VARIABLE` (copiloto.py) y servidos por /copiloto/vistas.
   Sumar/editar un chip = editar esa lista + changelog.
3. **Plantillas de formato por tipo de pregunta** (ranking / estado de papel /
   comparación / screening). Estado: cubierto por el método Minto + ejemplos
   MAL/BIEN + los prompts de los chips (que fijan formato por consulta).
4. **Escenarios deterministas** ("¿qué pasa si el CCL sube 5%?") — código
   computa, el modelo narra. DIFERIDO: antesala del P5.
Ya cubiertos por diseño previo: los 5 componentes (rol/contexto/tarea/
restricciones/formato), verificación de outputs (automatizada, mejor que el
protocolo manual del libro), anti-patrón de datos en tiempo real (el modelo
solo ve lo inyectado).

## Changelog del asistente (obligatorio, con fecha)

### 2026-07-11 — v1.17 (tablas, ruedas, rankings deterministas, fix del guardrail)
- **[bugfix]** El guardrail de jerga tenía un bug de regex: "ret_año%" no
  matcheaba por el "%" pegado — por eso siguió pasando jerga tras v1.15.
  Corregido + "columna"/"header" al lexicón + el DERRAME de razonamiento
  visible ("Corrijo:", "— no,") también dispara la reescritura.
- **[contexto +]** Retornos por RUEDAS: `ret_15ruedas%` (ya venía del scanner,
  no lo exponíamos) + `ret_30ruedas%`/`ret_45ruedas%` calculados de la serie
  (1 query cacheada). Pedido de la mesa: el lente de trading.
- **[contexto +]** Bloque `[rankings ya calculados]`: top/peores del año, mes,
  semana y día ordenados por CÓDIGO (caso real: el modelo salteó a SNDK +707%
  en el top del año — ordenar 187 filas a ojo es lo que peor hace).
- **[prompt +]** Definición de REPUNTE de la mesa (cae en el tramo largo Y se
  dio vuelta en 15 ruedas + semana; una semana verde sola = "rebote de
  corto") · listas de papeles con datos → TABLA markdown simple (máx 4
  columnas) + una línea de lectura. Chip Rezagados actualizado a este formato.
- **[UI]** El panel renderiza tablas markdown (datos en tabla, lectura abajo).

### 2026-07-11 — v1.16 (chips de mesa + audiencia por rol)
- **[producto +]** Biblioteca de consultas de mesa: 5 chips de un click en el
  panel (Papeles de IA · Argentina · En zona de decisión · Rezagados
  repuntando · Voladores del año — elegidos por el user). Prompts curados y
  versionados en `_CHIPS_RENTA_VARIABLE`; el panel los recibe por
  /copiloto/vistas.
- **[prompt +]** Audiencia por rol RBAC (tono definido por el user): trader
  seco y numérico · sales explicado con frases repetibles al cliente · admin
  neutro.

### 2026-07-11 — v1.15 (guardrail de jerga + el plazo de la pregunta manda)
- **[agente +]** La jerga interna dejó de ser una regla de prompt y pasó a ser
  GUARDRAIL estructural: `_jerga_en_respuesta()` detecta por código headers/
  términos del sistema colados en la respuesta ("ret_7d", "monto_usd_ny"…) y
  dispara la misma auto-corrección que los números sin respaldo (un solo
  reintento cubre ambos). Permitido solo si el usuario usó el término en su
  pregunta. (Shadow: el prompt solo no alcanzaba — el modelo lo rompía cada
  tanto.)
- **[prompt +]** El PLAZO que nombra la pregunta manda la conclusión; los
  demás plazos entran como matiz al final. Caso real como ejemplo MAL/BIEN:
  "¿cómo vienen las del espacio este 2026?" → la respuesta es la del AÑO
  (+11%), no la del día.

### 2026-07-11 — v1.14 (método de respuesta — pirámide de Minto)
- **[prompt ~]** Las reglas de estilo se consolidaron en un MÉTODO general de
  armado de respuesta (pedido del user: general, no guionado; fundado en
  Pyramid Principle/BLUF y en los pilotos de LLM del FCA): (1) conclusión
  primero en una frase simple + máx 3 apoyos; (2) plazos con sentido — corto Y
  largo, y decir si la historia cambia según el plazo; (3) estadística para
  PENSAR pero traducida al hablar (beta/correlación/z-score jamás nombrados
  salvo pedido explícito); (4) una idea por frase. Ejemplo MAL/BIEN nuevo con
  el caso real de META (metralleta de cifras → narrativa de mesa).

### 2026-07-11 — v1.13 (auto-corrección + solo lo pedido)
- **[agente +]** Reflexion: si la verificación encuentra números sin respaldo,
  la respuesta NO se muestra — el modelo recibe su propia respuesta con la
  lista exacta de números que no cierran y la reescribe con datos reales (1
  reintento; se queda la mejor). El costo extra solo se paga cuando falla.
- **[verificación]** La advertencia ahora dice CUÁLES números no verificó
  ("⚠ No pude verificar: 325M, 8.8M"). Y entiende abreviaciones K/M/B
  ("325M" ≈ 325.432.132 → respaldado) — los 2 falsos positivos del shadow
  eran montos abreviados.
- **[prompt +]** Responder SOLO lo pedido — sin métricas de yapa (el shadow
  metía volumen en una pregunta de pivots) + ejemplo MAL/BIEN del caso real.

### 2026-07-11 — v1.12 (números verificados + pivots traducidos)
- **[bug real]** El modelo citaba la COLUMNA equivocada y volteaba signos
  (dijo "MU ytd −5.13" cuando −5.13 es el MES y el año es +243; "TGT −38"
  cuando es +38). La tabla estaba bien; el modelo se perdía entre 26 headers
  crípticos (`adr_ret_mtd_pct` vs `ytd`).
- **[contexto]** Headers del TSV renombrados a lenguaje claro e inconfundible:
  `ret_mes%`, `ret_año%`, `precio_usd_ny`, `var_dia_usd%`, `zona_piv_año`…
- **[verificación +]** Chequeo mecánico anti-alucinación: cada número de la
  respuesta se busca en el contexto enviado (tolerancia de redondeo; ignora
  rankings/años). Números sin respaldo → warning en logs + `⚠ tomalo con
  pinzas` visible en el panel del copiloto (nivel informar, no bloquea).
- **[prompt +]** Ejemplos few-shot de estilo (MAL/BIEN) + pivots como CONTEXTO
  jamás vocabulario: nunca decir PP/R1/S2 al usuario (salvo que él los nombre);
  traducir la zona a lectura de mesa y dar precios concretos, no nomenclatura.
  Semántica del user: R3/S3 extremos, R1/S1 puede seguir o rebotar al PP,
  R2/S2 tendencia clara.
- **[evals]** Caso `ticker_natural` (la respuesta técnica de GGAL, fallo real).

### 2026-07-11 — v1.11 (voz de operador)
- **[prompt +]** Regla de identidad: habla como OPERADOR, no como analista de
  datos — prohibido mencionar columnas/jerga interna ("es_ia",
  "adr_ret_mtd_pct", "de la tabla…"); criterio del ranking en UNA línea de
  lenguaje de mesa; si piden N, exactamente N; sin resumen redundante al
  final. (Shadow: la respuesta recitaba la mecánica y cerraba repitiendo la
  conclusión.)
- **[evals]** Jerga interna prohibida en los 4 casos de ranking/sector.

### 2026-07-11 — v1.10 (thinking bajo control)
- **[bug raíz]** Los v4 traen `thinking` DEFAULT ENABLED (verificado contra la
  doc del proveedor): el copiloto razonaba sin pedirlo — tokens invisibles,
  "respuestas vacías" al ras del techo, y el razonamiento derramado dentro de
  una respuesta del shadow (ranking con autocorrecciones interminables).
- **[gateway]** Switch `thinking` explícito POR TAREA: copiloto/controles/
  smoke → disabled; triage → enabled a propósito (diagnóstico — cierra el
  cabo suelto del P2). `reasoning_content` se captura y guarda en
  `ia.trazas.razonamiento` (cap 2000) → visible en el DETALLE del panel:
  ahora se puede debuggear CÓMO razonó cada llamada. Requiere apply_schema.
- **[prompt +]** Brevedad dura: directo al resultado, sin cálculos intermedios
  ni correcciones, ~12 líneas máx, ranking = 1 línea de criterio + lista.
- **[evals]** Caso nuevo `ranking_breve` (el fallo real) + chequeo `max_chars`
  en el runner (detecta derrames de razonamiento).

### 2026-07-11 — v1.9 (excepciones de límite por usuario)
- **[gateway]** Límite diario PERSONAL por usuario (clave
  `budget_dia_usuario:<email>` en ia.config): pisa el tope general solo para
  ese email — ej. el admin se da más margen que la mesa. Siempre ≤ global
  (techo duro intacto). Editable en el cuadrante PRESUPUESTO (solo admin,
  auditado); `POST /api/ia/presupuesto/usuario` (valor null = borrar).

### 2026-07-11 — v1.8 (saldo real del proveedor)
- **[observabilidad]** `GET /api/ia/saldo` + cuadrante PRESUPUESTO del panel:
  saldo REAL de la cuenta DeepSeek (`GET /user/balance`, verificado contra la
  doc del proveedor 2026-07-11; cache 5 min). Se muestra plata (total/cargado/
  otorgado por moneda) + flag oficial "alcanza para operar". "Tokens
  restantes" NO se muestra a propósito: el proveedor no lo expone y
  convertir plata→tokens sería inferir precios/mix de modelos (REGLA #2).

### 2026-07-11 — v1.7 (errores claros + trazas con contenido)
- **[UX]** El panel dice CUÁL degradación fue: "alcanzaste TU límite diario"
  (presupuesto_usuario) vs "el sistema alcanzó su tope" (presupuesto_global)
  vs "sin datos" — chau "IA no disponible" genérico. El copiloto chequea el
  presupuesto ANTES de armar el contexto (`core.ai.motivo_presupuesto`).
- **[observabilidad]** `ia.trazas` ganó `detalle` (la pregunta) y `respuesta`
  (extracto, cap 1500): cada llamada del copiloto queda auditable con su
  contenido. Requiere apply_schema.
- **[UI Manager]** OBSERVABILIDAD → IA rediseñada en 4 cuadrantes: presupuesto
  (ver/editar + usado hoy) · por tarea/por día (tabs) · últimas llamadas
  (click) · detalle de la llamada elegida (pedido/respuesta/error).

### 2026-07-11 — v1.6 (pulso + evals)
- **[contexto +]** Bloque `[pulso por rubro]`: retornos 1d/WTD/MTD/YTD por
  rubro YA calculados (ponderados por volumen USD, mismo criterio que el PULSO
  de la vista). Regla de oro 1: para preguntas de mercado/sector el modelo
  narra números deterministas en vez de promediar 187 filas a mano.
- **[gateway]** `max_tokens` 2000 → 3000 (trazas mostraron respuestas de 1796
  al ras del techo + una vacía).
- **[evals]** Nace el eval set (Fase 0.4): 6 casos (4 fallos reales del
  shadow + 2 adversos de diseño) + runner `scripts/eval_copiloto.py`.

### 2026-07-11 — v1.5 (gateway)
- **[gateway]** Presupuestos de tokens editables desde Manager →
  OBSERVABILIDAD → IA (tabla `ia.config`, solo admin, auditado). El GLOBAL
  diario es techo duro del sistema; el tope por usuario no puede superarlo.
  Afecta la disponibilidad del asistente: presupuesto agotado = "IA no
  disponible" hasta medianoche UTC o hasta que el admin suba el tope.

### 2026-07-11 — v1.4 (presupuesto)
- **[medición]** Costo real por pregunta: ~22k tokens de input (187 filas ×
  26 columnas), medido en `ia.trazas`. El shadow agotó el tope por usuario
  (200k = ~9 preguntas) → "IA no disponible" a media tarde.
- **[gateway]** Presupuesto diario por usuario: default 200k → **1M** (~45
  preguntas ≈ centavos en flash). Global sigue en 2M. Env:
  `AI_BUDGET_TOKENS_DIA_USUARIO`.
- **[contexto −]** Números sin separador de miles en el TSV ("15234.50", no
  "15,234.50") — tokeniza mejor con ~4900 celdas por pregunta.

### 2026-07-11 — v1.3
- **[fix detección]** Palabras comunes que colisionan con tickers ("de" →
  Deere) solo matchean escritas en MAYÚSCULAS (`_TOKENS_AMBIGUOS`). Caso real
  del shadow: "acciones de IA" inyectaba el detalle de DE.
- **[prompt +]** Pedidos de recomendación: ranking objetivo con criterio
  explícito en vez de "no puedo recomendar". Sin consejo de inversión.

### 2026-07-11 — v1.2 (primer loop del shadow)
- **[contexto +]** Columnas `es_ia` y `rubro` (la pregunta real "¿qué acciones
  hay de IA?" reveló que faltaban — el modelo improvisaba por nombre).
- **[contexto +]** Zonas de pivots `piv_anual` / `piv_mensual` para TODO el
  universo (2 queries agregadas cacheadas 15 min — no 187 llamadas).
- **[prompt +]** Lectura de pivots de la mesa (conocimiento no inferible,
  aportado por el user): >R3/<S3 subió/cayó muchísimo · R2-R3/S3-S2 tendencia
  clara · R1-R2/S2-S1 movió algo · PP zona ideal de decisión. Uso implícito,
  sin listar niveles salvo pedido.
- **[UI −]** Línea de fuente (📊 vista · filas · hora) fuera de la respuesta;
  el disclaimer fijo del panel cubre la marca de origen IA.

### 2026-07-11 — v1.1 (vista completa)
- **[alcance]** De "tabla CEDEARs" a VISTA Renta Variable completa (ambas tabs).
- **[contexto +]** CCL live (siempre) + detalle por ticker mencionado: pivots
  4 marcos, quant, últimos 15 retornos, fundamentals Refinitiv (cap 3 tickers,
  detección determinista sin LLM).
- **[UI]** Botón "Consultale a la IA" (antes "IA"); luego movido a la barra de
  tabs, margen derecho. Negritas renderizadas. Bienvenida "¿En qué puedo
  ayudarte?". Alias `cedears` creado y borrado tras confirmar el deploy.

### 2026-07-11 — v1 (nacimiento)
- Copiloto CONTEXTUAL por vista (decisión del user: NO chatbot global). Una
  llamada flash por pregunta; datos armados server-side desde el service
  @cached; TSV; gate doble `ia` + módulo de la vista; historial corto
  client-side (4 pares); 👍/👎 → `ia.trazas.feedback`; degradación ok=False.
- Colateral: el copiloto expuso series rotas en `mercado.precios_acciones`
  (2025 incompleto 116/187, split CRWD, vela basura HON) → backfill 2024+ +
  auto-reparación de splits en `jobs/precios_acciones_daily`.
