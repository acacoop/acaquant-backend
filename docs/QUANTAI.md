# QuantAI — Roadmap vivo de IA en ACAquant

> **REGLA DE ESTE DOCUMENTO (leer antes de tocar nada):** es un documento VIVO de
> estado. Cada cosa que se termina se MUEVE a "Hecho" (una línea) o se BORRA su
> detalle; cada cosa que se descarta se borra con una línea de porqué; cada
> decisión nueva se asienta. En cualquier sesión futura, leer este archivo debe
> alcanzar para saber exactamente dónde está parado el proyecto. Si se trabaja
> en algo de acá y no se actualiza este archivo en el mismo cambio, el trabajo
> está INCOMPLETO.

Sin plazos ni fechas a propósito: el orden es por dependencia técnica, no por
calendario.

---

## Decisiones tomadas (no re-litigar sin el user)

- **Proveedor: DeepSeek** (API OpenAI-compatible, `https://api.deepseek.com`).
  Modelo default `deepseek-v4-flash` (barato, sobra para redacción/clasificación);
  `deepseek-v4-pro` reservado para razonamiento pesado (triage, analista).
  Thinking mode: apagado para tareas simples, prendido para diagnóstico.
  A nuestro volumen el costo es despreciable (<$1/mes los pipelines) — las
  decisiones se toman por calidad/privacidad, no por precio.
- **Marca AI = módulo `ia` del RBAC existente.** Se agrega a `MODULES`
  (core/roles.py) + matriz editable en Manager → ROLES Y PERMISOS. El frontend
  muestra features de IA solo si `/api/me` trae `ia`; el backend gatea los
  endpoints con `require_module("ia")` (el front es cosmético, el gate real es
  el server). Sirve de rollout gradual (primero el admin, después la mesa),
  control de costos por usuario y kill switch (sacar el módulo = la IA
  desaparece en ~60s, TTL del cache de roles). El rol `invitado` JAMÁS tiene
  `ia` (default-deny del portal público, REGLA #8).
- **Sin MCP como base.** El copiloto y todo lo interactivo va NATIVO en la app
  (endpoints propios + panel propio). El MCP server existente queda como está
  (conector de claude.ai para RV) pero no es la plataforma de esto.
- **Reglas de oro (aplican a TODOS los proyectos):**
  1. La IA nunca es la fuente de un número: todo dato sale de una tool que
     llama a `api/services` / SQL. La IA redacta, traduce, agrupa, propone.
  2. La IA nunca escribe a prod: las escrituras salen de código determinista o
     de un click humano (human-in-the-loop). Errores conocidos → playbook
     determinista, no LLM (precedente: cuarentena de símbolos ROFEX).
  3. Datos de clientes (nombres/cuentas/montos) NO salen al proveedor salvo
     decisión explícita del user por proyecto; donde haga falta, anonimizar
     antes de mandar y re-insertar después (patrón del proyecto 6).
  4. Todo degrada con gracia: si DeepSeek no responde, cada feature cae a su
     versión determinista o desaparece — nunca rompe la vista/el job.

---

## Estado actual

- **Hecho (previo al roadmap):**
  - Auto-control de calidad de datos (jobs/controles_datos + tab CONTROLES en
    OBSERVABILIDAD) y playbook determinista de cuarentena ROFEX — la base
    operativa sobre la que se montan Briefing y Triage.
- **`DEEPSEEK_API_KEY` seteada + SMOKE OK (2026-07-10):** gateway operativo de
  punta a punta en el Droplet (completion real + traza en `ia.trazas`).
  **VERIFICADO contra el proveedor:** los IDs `deepseek-v4-flash` y
  `deepseek-v4-pro` existen tal cual (GET /models) — ya no es hipótesis.
- **Fase 0: 3 de 4 hechos** (gateway, RBAC, observabilidad). Falta solo la
  suite de evals (punto 4), diferida A PROPÓSITO hasta tener outputs reales
  que congelar como casos (hoy no hay ninguna tarea LLM corriendo en prod).
- **Cabos sueltos al cierre 2026-07-10:**
  1. Validar la frescura del feed MAE (PC oficina) a las 10:00 ART — el propio
     modal del briefing lo revela: si muestra "aún sin operaciones" a las 10 y
     precio a las 10:15, se corre la hora o se acepta el delay.
  2. `.env` del Droplet tiene una línea mal formada (warning python-dotenv
     "line 33") — no rompe, pero si esa línea era una var real se está
     ignorando en silencio. Ver con `sed -n '33p' /root/TradingAV/.env`.

---

## Fase 0 — Fundación (prerequisito de todo)

**Estado: EN CURSO**

1. ~~**`core/ai.py` — el gateway único de IA.**~~ **HECHO (2026-07-10).**
   Toda llamada a un LLM pasa por `core.ai.completar(tarea, system=, user=,
   usuario=)`: tareas registradas (`_TAREAS`: tier flash/pro, max_tokens,
   timeout), presupuesto diario de tokens global y por usuario (contra
   `ia.trazas`; superado → degrada), 1 retry ante timeout/5xx, traza SQL de
   cada llamada, contrato "nunca levanta" (devuelve None). `ai_resumen` migrado
   adentro (quedó como prompt de la tarea `controles_resumen`). Modelos
   env-overridables (`AI_MODEL_FLASH`/`AI_MODEL_PRO`); thinking mode se cablea
   con la primera tarea pro (P2) verificando el API del proveedor. Smoke E2E:
   `scripts/smoke_ai.py`.
2. ~~**Módulo `ia` en el RBAC**~~ **HECHO (2026-07-10).** `ia` en `MODULES`
   (core/roles.py) + prefijo `/api/ia` → `ia` en `ENDPOINT_MODULE_PREFIXES`
   (api/auth.py): todo endpoint futuro bajo `/api/ia` nace default-deny.
   `invitado` NUNCA lo tiene (test que lo congela). Default solo `admin`;
   como la matriz de prod pisa el default, la activación real es el tilde
   del admin en el panel (ver "Próximo paso inmediato").
3. ~~**Observabilidad de la IA desde el día 1**~~ **HECHO (2026-07-10).**
   `ia.trazas` + registro desde el gateway (quién, tarea, modelo, tokens
   in/out, latencia, éxito/fallo, columna `feedback` para el 👍/👎 futuro) +
   vista: `GET /api/ia/observabilidad` (service `api/services/ia_obs.py`,
   gate módulo `ia`) → pill **IA** en Manager → OBSERVABILIDAD (resumen de
   hoy con % del presupuesto, por tarea, por día, últimas llamadas). La pill
   solo aparece con el módulo `ia` en /api/me.
4. **Suite de evaluación mínima:** por cada tarea de IA, un set chico de casos
   de prueba (input real → output esperado/criterios) que se corre al cambiar
   un prompt o modelo. Empieza siendo un archivo de casos + un script; crece
   con los fallos reales que se vayan encontrando.

---

## Proyectos elegidos (orden = dependencias)

### P1 — Briefing de apertura automático
**Estado: v2 COMPLETA (backend + modal), en shadow · falta solo el bloque
Agenda (fuente)** · Tipo: modal in-app · Canal: SOLO interno (decisión del user
2026-07: NADA de Telegram para esto)

**Decisión 2026-07-10 (user): la v1/v2 son SIN LLM.** El contenido son solo
números → regla de oro 1: se renderiza determinista. La capa de redacción IA se
suma ARRIBA cuando el briefing incorpore narrativa; este cartel ES su fallback.

**v1 (base):** `api/services/briefing.py` (compute-on-read, sin cron ni tabla:
reusa `home.market_quotes`, `valuaciones.dolar_oficial_live`, `macro.series_macro`
DOLAR, `valuaciones.dolar`; "rueda anterior" = últimos días CON datos → feriados
gratis) + `GET /api/ia/briefing` (gate `ia`) + `BriefingModal` en HOME.

**v2 (2026-07-10, backend hecho — falta rehacer el modal):** rediseño de contenido
pedido por la mesa, medido con diags antes de construir. Modelo de columnas
UNIFORME `HOY · 1D · WTD · MTD` para toda fila. Bloques del payload:
- **`futuros`** (10, agrupados Índices US/Energía/Metales/Granos/Cripto): los 10 de
  `HOME_FUTUROS` ya se ingerían; se extendió `jobs/market_anchors` para cubrirlos
  (+ ancla nueva `anchor_wtd` = week-to-date real, además de `anchor_7d` rolling
  que se mantiene para /argy). 1D=pct_day, WTD/MTD desde anclas del doc.
- **`oficial`** (mayorista MAE live + A3500) y **`financieros`** (MEP/CCL): WTD/MTD
  se calculan al vuelo desde su propio histórico (sirven aunque hoy no opere —
  se anclan al último cierre). Mayorista sin histórico → HOY "Sin Ops", WTD/MTD None.
- **`pagan_hoy`**: bonos que pagan cupón/amort/vto hoy (título SIN "en cartera" pero
  FILTRADO a lo held). `acreencias.bonos_pagan_en_fecha`: ESTRUCTURAL sobre
  `mercado.curvas` (existe el flujo con fecha == hoy, no lo valúa → un CER que paga
  hoy aparece aunque su CER de liq no esté publicado) cruzado con el último AUM.
  Cacheado por día (el poll no recomputa). NO toca la tabla de acreencias ni el job.
  Pendiente aparte (pedido del user): llevar `titulos_sin_flujo` (bonos en cartera
  SIN flujo) a un control automático de health (hoy vive solo en el Manager).
- **`agenda` — DIFERIDO por fuente muerta.** `home.market_calendar` (Finnhub free)
  dejó de servir datos ~2026-04; última ventana 17/04→17/06. Reemplazo decidido:
  **FMP** (`/api/v3/economic_calendar`, free 250 req/día, verificado). PENDIENTE:
  `FMP_API_KEY` en el Droplet (REGLA #6) → escribir `core/fmp.py` + reapuntar
  `jobs/economic_calendar` a FMP (misma tabla) + sumar bloque `agenda` al briefing.

Los diags de diseño del briefing (`diag_briefing_*`) se borraron al cerrar el
contenido (REGLA #5). Si se retoma el bloque Agenda, se re-arma uno puntual.

**Modal (acaquant-web `briefing-modal.tsx`):** rehecho al shape v2 — tabla
uniforme de 4 columnas, futuros agrupados, mayorista "Sin Ops", bloque "Bonos que
pagan hoy" (solo si hay). Aparece 10:00 ART L-V (gate `ia`), dismiss por día,
botón ☀ BRIEFING para re-lectura.

**Pendientes:** (1) Agenda vía FMP (ver arriba — necesita `FMP_API_KEY`).
(2) Health: `titulos_sin_flujo` → control automático en `controles_datos`.
(3) frescura del feed MAE a las 10:00 (PC oficina). (4) verificar el cruce de
"bonos que pagan hoy" el día que pague alguno (hoy 10/07 no paga ninguno).

Cron pre-apertura que junta lo YA ingerido (ADRs, dólar, riesgo país,
economic_calendar, news_headlines, acreencias próximas, estado de controles) en
un JSON, el LLM lo REDACTA como informe de mesa de ~12 líneas y se PERSISTE
(tabla SQL, 1 fila por día). La IA no busca ni decide: narra datos verificados
(workflow determinista con un paso de redacción — no es un agente, y no debe
serlo).

**Entrega — modal "Briefing" en HOME:** al abrir acaquant, al usuario (con
módulo `ia`) le aparece una ventana centrada con el briefing del día y un botón
**"No volver a mostrar"** que lo silencia POR ESE DÍA (dismiss persistido por
usuario+fecha — localStorage alcanza para v1; al día siguiente reaparece con el
briefing nuevo). Si el briefing del día aún no se generó o el LLM falló, el
modal no aparece (failing gracefully: nunca una ventana vacía). Re-lectura
manual: acceso desde HOME para volver a abrirlo aunque se haya descartado.
Éxito: que la mesa lo reclame el día que falte.

### P2 — Triage inteligente de incidentes
**Estado: v1 FUNCIONANDO (2026-07-10) — probado con fallas reales, falta shadow** ·
Tipo: watcher reactivo (NO batch — decisión del user) · Canal: Telegram
(+ OBSERVABILIDAD pendiente)

Sobre `manager.job_runs` fallados (status='error'): agrupa por FIRMA del error,
distingue HECHO de HIPÓTESIS y recomienda acción (modelo pro). **Nunca ejecuta nada.**

**Decisión del user 2026-07: REACTIVO PURO, no batch.** Un cron corto (cada 10 min)
chequea fallas nuevas; el chequeo es una query SQL (gratis) y el token sale SOLO ante
una firma nueva. **4 guardas de costo:** (1) dedup por firma — una firma conocida no
se re-diagnostica (0 tokens), solo suma ocurrencias; (2) watermark — cada corrida
procesa solo lo nuevo (batchea ráfagas: crashloop = 1 diagnóstico); (3) presupuesto
(ya en core/ai) — superado → firma queda 'nuevo' sin diagnóstico (degrada); (4)
severidad — solo crashes. Loop de mejora: cuando un patrón se repite, el objetivo es
codificar un playbook determinista → esa falla sale del camino del LLM para siempre
(más determinista, menos tokens con el tiempo).

**Construido:** `jobs/triage.py` (`--dry-run` = prueba sin tokens; `--force` +
`--lookback-min N` = re-diagnóstico/inspección ignorando dedup y watermark) +
`ia.triage_incidentes` (memoria de firmas/diagnósticos) + `ia.triage_estado`
(watermark) + tarea `triage_incidente` (pro) en core/ai + cron cada 10 min. El
contexto que va al modelo = errores acumulados de `job_runs` + el log real del job
(`logs/<tipo>.log`, que escribe `run_job.sh` con stdout+stderr — más rico que lo que
el job guarda en job_runs). Privacidad: logs = dato técnico; scrub de emails/CUITs;
log tratado como DATO hostil (anti prompt-injection). Debug del gateway:
`scripts/diag_ia_trazas.py`.

**Lecciones (verificadas con `ia.trazas`, la medición como piedra angular):**
- `deepseek-v4-pro` RAZONA y el razonamiento cuenta como output → `max_tokens`
  chico (700) = se queda sin lugar para la respuesta y vuelve VACÍA. Subido a 2500.
  Regla general para tareas `pro`: dar techo de salida amplio. **Cabo suelto:**
  `controles_resumen` (flash, 800) tiene el MISMO síntoma con inputs grandes →
  subirle el max_tokens también.
- Costo real medido: ~13k tokens en un día entero de pruebas (de 2M de presupuesto) →
  centavos. El pipeline cuesta lo que el doc decía (<$1/mes).

**Pendiente:** correr en shadow unos días (leer los diagnósticos antes de abrirlo) ·
tab TRIAGE en OBSERVABILIDAD (hoy solo Telegram) · thinking mode del pro (sin cablear,
REGLA #2) · sumar 'partial' además de 'error' si hace falta · subir max_tokens de
`controles_resumen`.

### P3 — Copiloto de Mesa
**Estado: v1.1 DEPLOYADA y confirmada (2026-07-11) — EN SHADOW (admin)** ·
Tipo: copiloto contextual por vista · Gate: `ia` + módulo RBAC de la vista

**Decisión 2026-07-11 (user): NO chatbot global — copiloto CONTEXTUAL por
tabla.** Cada tabla de mercado tiene su botón IA y el panel responde SOLO sobre
los datos de ESA tabla. Es "workflows antes que agentes" aplicado al copiloto:
la vista determina el contexto → UNA llamada LLM por pregunta (sin loop ReAct,
sin catálogo de tools, más barato y evaluable). El chatbot global con tools por
RBAC queda como evolución natural NO planificada: el armador de contexto de
cada vista ES la tool futura. Primera vista: **CEDEARs** (decisión user).
Conversación corta: últimos 4 pares pregunta/respuesta, viven en el cliente —
v1 no persiste historial.

**Diseño v1 (construido):**
- `api/services/copiloto.py` — registro `VISTAS`: cada vista declara fetch (el
  MISMO service `@cached` que alimenta la tabla — refetch gratis), subset de
  columnas relevantes, módulo RBAC y reglas del dominio para el system prompt
  (qué significa cada columna — contra la corrección silenciosa).
- **Los datos JAMÁS viajan del frontend**: el browser manda `{vista, pregunta,
  historial}`; el server arma el contexto. Anti prompt-injection + datos reales
  garantizados + no se paga el upload.
- Serialización **TSV, no JSON** (~2-3× menos tokens de input); cap 400 filas;
  celdas sanitizadas (una celda jamás rompe el TSV ni inyecta líneas).
- Endpoints: `GET /api/ia/copiloto/vistas` (vistas habilitadas; el front lo usa
  de probe — 403 = sin `ia` = botón oculto), `POST /api/ia/copiloto`,
  `POST /api/ia/copiloto/feedback` (👍/👎 → `ia.trazas.feedback`; el gateway
  ganó `completar_con_traza()` que devuelve el id de la traza).
- Gate doble estructural: módulo `ia` (montaje `_IA`) + módulo de la vista
  (`has_access`) — si tu rol no ve la tabla, el copiloto no existe ahí.
- Tarea `copiloto_vista` (flash, max_tokens 2000 — lección del P2). Control de
  gasto = presupuesto por usuario ya existente del gateway (no se hizo contador
  de mensajes aparte — si el uso real lo pide, se agrega).
- v1 SOLO vistas de mercado → datos públicos, nada que anonimizar (regla de
  oro 3 ni se activa). Vistas con datos de clientes exigirán esa capa ANTES.
- Degradación: todo fallo → `ok=false` con motivo; el panel muestra "IA no
  disponible", la tabla ni se entera.
- Frontend (acaquant-web): `ia-vista-panel.tsx` — drawer lateral montado en el
  shell de Renta Variable (presente en ambas tabs), oculto sin módulo `ia`,
  con fuente de datos visible, 👍/👎 y botón "Consultale a la IA".

**v1.1 (2026-07-11, pedido del user): copiloto de la VISTA completa, no solo
la tabla.** La vista `renta_variable` suma al contexto: **CCL live** (siempre,
1 línea) y **detalle por ticker** — pivots 4 marcos, quant (beta/corr vs
SPY/QQQ, vol, z-score), últimos 15 retornos diarios y fundamentals Refinitiv
(si la empresa está en `research.companies`; hoy cobertura ~1 empresa, RKLB —
crece sola al cargar más). Cómo sin explotar tokens: **detección determinista
de tickers en la pregunta** (match de tokens contra ticker/underlying del
universo, sin LLM, cap 3) → solo los bloques de los tickers nombrados entran
al contexto. El system prompt le enseña a pedir el ticker exacto cuando el
detalle no está. Fuera del alcance de esta vista (son otras páginas): Trade
Lab (admin), intradía/time-sales (/trading), Mesa de Estrategia (/retorno).
Alias transitorio `cedears` borrado tras confirmar el deploy (2026-07-11).

**Incidente colateral resuelto (2026-07-11): pivots/quant con series rotas.**
El copiloto expuso que `mercado.precios_acciones` tenía 116/187 series
arrancando 2025-05-20 (vela anual incompleta), CRWD en escala pre-split y una
vela basura en HON. Fix en `jobs/precios_acciones_daily`: `--backfill` scopeado
desde 2024-01-01 (corrido y verificado con diag, ya borrado) + auto-reparación
permanente en el daily (vela guardada difiere >10% de Yahoo = re-ajuste/split →
re-backfill del ticker en el acto).

**Pendientes:** shadow en curso — el admin la usa unos días, deja 👍/👎 y se
leen `ia.trazas` antes de abrirla a la mesa · eval set de `copiloto_vista` con
las preguntas reales del shadow (arranca la Fase 0.4) · replicar a más vistas
(renta fija, opciones…) recién después del shadow · hipótesis SIN verificar:
el context caching de DeepSeek abarataría preguntas sucesivas — medir en
`ia.trazas` antes de contar con eso.

### P4 — Prep de reuniones comerciales
**Estado: PENDIENTE** · Tipo: informe con receta (workflow sobre el copiloto) ·
Gate: `ia` + RBAC comercial

"Preparame la reunión con [cuenta]": tenencia, PnL, actividad, acreencias
próximas, estado comercial, + talking points según reglas definidas con el
equipo (ej. cash ocioso → proponer remuneración). Es el copiloto con guion
fijo: 6 llamadas a services predefinidas + redacción. **Privacidad:** los
nombres del cliente se ANONIMIZAN antes de salir al proveedor (CLIENTE_A) y se
re-insertan server-side en el texto final — al proveedor solo llegan números y
placeholders. Éxito medible en reactivación de cuentas DORMIDAS.

### P5 — Analista ad-hoc de datos
**Estado: PENDIENTE** · Tipo: agente con generación de SQL · Gate: `ia`,
inicialmente solo admin

Preguntas libres sobre la base ("volumen por nivel_1 mensual, este año vs el
pasado") → el agente escribe SQL, ejecuta, devuelve tabla + gráfico + lectura.
La JAULA es el 80% del proyecto: rol Postgres read-only sobre un subset
whitelisted (decidir si comitentes se expone con nombres o vía vista
anonimizada), timeout, límite de filas, log de toda query ejecutada, y SQL
siempre visible en la respuesta (auditable). Riesgo principal: corrección
silenciosa (query que corre pero cuenta mal) → las reglas de negocio críticas
van en el contexto del agente (volumen excluye `es_cierre`, AuM filtra
`aum='si'`, etapa IS DISTINCT FROM…) y las métricas core se sirven desde
vistas pre-armadas, no calculadas desde cero. Va último: exige el criterio y
la infraestructura de evaluación que los proyectos anteriores construyen.

---

## Principios de ingeniería (guían TODO el desarrollo)

Organizado por disciplina. Para cada concepto la POSICIÓN de QuantAI:
**ADOPTAR YA** / **DIFERIDO** (con la condición explícita que lo activa) /
**NO APLICA** (con el porqué). Si un diseño contradice esto, se replantea el
diseño — o se actualiza esta sección con la decisión nueva.

### Arquitectura y orquestación
- **Workflows antes que agentes — ADOPTAR YA.** Tarea con pasos conocidos
  (briefing, prep de reunión, triage) = workflow determinista con pasos de LLM:
  más barato, predecible, testeable. Agente (el modelo decide qué hacer) solo
  donde la entrada es abierta de verdad: copiloto (ReAct simple: razonar →
  tool → responder) y analista (planner-executor: planea la query, la ejecuta,
  lee el resultado).
- **Multi-agente / swarms / coordinación (manager, jerárquica, actor-critic) —
  DIFERIDO.** Un solo agente con buenas tools cubre todo el alcance actual.
  Condición de disparo: una tarea real donde un agente solo demuestre no
  alcanzar (ej. el analista necesitando un "revisor" independiente de sus
  queries — sería el primer caso legítimo de 2 agentes: generador + crítico).
  Actor frameworks (Ray/Akka), message brokers, A2A: NO APLICA a esta escala —
  nuestro "motor de orquestación" son los crons + jobs que ya existen.
- **Model selection por tarea, no por moda — ADOPTAR YA.** Flash para
  redactar/clasificar; pro + thinking para diagnóstico y SQL. Revisar con datos
  de la tabla de observabilidad, no por sensación.
- **Tools chicas, puras y bien descriptas — ADOPTAR YA.** Cada tool hace una
  cosa; la descripción dice CUÁNDO usarla. Selección de tools estándar (lista
  plana curada): semántica/jerárquica DIFERIDO hasta superar ~30 tools. Las
  tools heredan el RBAC — el permiso es estructural, jamás un "no muestres
  esto" en el prompt.

### Conocimiento y memoria
- **Context engineering explícito — ADOPTAR YA.** Lo que el modelo necesita se
  le da curado (reglas de negocio, definiciones, datos del caso); nunca la base
  entera ni su memoria de internet. Prompts versionados en el repo como código.
  Gestión de context window: los payloads se capan y priorizan (lo nuevo > lo
  viejo, lo anómalo > lo normal) — ya practicado en ai_resumen.
- **Memoria conversacional (corto plazo) — ADOPTAR con el copiloto.** Historial
  por usuario en SQL, con ventana acotada.
- **Full-text search antes que semántica — ADOPTAR YA.** Postgres ya lo da
  gratis; para buscar en operaciones/logs/noticias alcanza y es exacto.
- **Vector stores / RAG / semantic search — DIFERIDO.** Nuestro patrón dominante
  es tool-calling sobre datos ESTRUCTURADOS (no hace falta RAG para leer una
  tabla). Condición de disparo: el primer corpus NO estructurado de tamaño real
  — ej. base educativa del copiloto (>50 documentos), histórico de research, o
  prospectos. Cuando pase: pgvector dentro del Postgres existente (no un vector
  store aparte — menos infra, misma casa).
- **Semantic experience memory / note-taking del agente — DIFERIDO** (versión
  liviana con el triage: registrar diagnósticos pasados y consultarlos como
  contexto "¿esto ya pasó?"; eso ES experience memory, sin llamarlo así).
- **Knowledge graphs / GraphRAG — NO APLICA.** Nuestro grafo YA existe y es
  relacional: cuentas↔operaciones↔tenencias↔instrumentos con FKs. Un KG
  paralelo duplicaría la verdad con riesgo de divergencia (el riesgo clásico de
  los grafos dinámicos). Se re-evalúa solo si aparece un dominio de relaciones
  no tabulares (ej. red de vínculos societarios entre emisores).

### Aprendizaje del sistema
- **Nonparametric primero — ADOPTAR YA.** El sistema aprende SIN tocar pesos:
  (a) exemplar learning = los mejores/peores outputs reales se convierten en
  ejemplos few-shot del prompt; (b) reflexion = en tareas de calidad crítica,
  un segundo paso del modelo critica su propio output contra una checklist
  antes de entregarlo (candidato: el SQL del analista); (c) experiential = el
  triage consulta diagnósticos previos. Todo esto es barato y reversible.
- **Fine-tuning (SFT/DPO) y small models — DIFERIDO.** Condición de disparo:
  una tarea estable, de alto volumen, con eval set maduro (>500 casos) donde el
  prompt engineering se haya estancado — el único candidato plausible a mediano
  plazo es la clasificación de noticias si se retoma ese proyecto. Hasta
  entonces es complejidad sin retorno.
- **RL con recompensas verificables — NO APLICA** a nuestra escala (es
  herramienta de labs que entrenan modelos, no de quienes los usan).

### Validación y medición
- **La medición es la piedra angular — ADOPTAR YA.** Ninguna feature de IA se
  declara "terminada" sin su eval set y su métrica de éxito definida ANTES de
  construir (cada proyecto la tiene en su sección).
- **Eval sets integrados al ciclo — ADOPTAR YA.** Cambio de prompt o modelo →
  correr el set de esa tarea. Cada fallo real de producción se convierte en
  caso nuevo (el set crece con la realidad, no con imaginación).
- **Evaluación por componente Y end-to-end — ADOPTAR con el copiloto.** Por
  componente: ¿eligió la tool correcta? ¿el SQL es correcto? End-to-end: ¿la
  respuesta final es correcta y consistente entre corridas? Chequeo de
  alucinación específico: todo número del output debe existir en el resultado
  de alguna tool llamada (verificable mecánicamente).
- **Inputs inesperados — ADOPTAR YA.** Cada eval set incluye casos adversos:
  pregunta fuera de alcance, datos vacíos, texto malicioso embebido.

### Monitoreo en producción
- **El monitoreo como fuente de aprendizaje — ADOPTAR YA.** La tabla de trazas
  de IA (Fase 0) es nuestro "Langfuse casero": tarea, modelo, tokens, latencia,
  costo, resultado, feedback. Visible en OBSERVABILIDAD. Stacks dedicados
  (OTel/Grafana/Langfuse/Phoenix): DIFERIDO — a nuestra escala la tabla SQL +
  la vista existente cumplen el mismo rol sin infra nueva; condición de disparo:
  volumen o multi-servicio que la tabla no aguante.
- **Shadow mode — ADOPTAR como práctica.** Todo pipeline nuevo corre N días
  generando output SIN entregarlo (se guarda y se revisa) antes de mostrarse.
  El briefing nace así: genera diario, lo lee solo el admin, y recién después
  se abre a la mesa.
- **Canary / rollout gradual — ADOPTAR YA:** es exactamente la marca AI
  (módulo `ia` primero al admin, después rol por rol).
- **Regression traces — ADOPTAR YA:** toda traza que produjo un output malo se
  archiva y entra al eval set (es la misma regla de "el set crece con la
  realidad", vista desde el monitoreo).
- **Feedback del usuario como señal — ADOPTAR YA:** 👍/👎 en cada output
  interactivo desde v1, guardado junto a la traza.
- **Distribution shift — ADOPTAR como control:** los datos argentinos cambian
  de régimen (canje, cepo, cambio de reglas) → cuando cambia el mundo, los
  prompts con reglas de negocio quedan viejos. Revisión disparada por eventos
  de mercado, no por calendario. Self-healing: ya tenemos el patrón (playbooks
  deterministas, cuarentena ROFEX) — la IA propone playbooks, no se auto-cura
  a sí misma.

### Bucles de mejora
- **Feedback pipeline — ADOPTAR YA:** trazas + 👍/👎 + fallos → revisión humana
  periódica → refinar prompt/tool → correr evals → desplegar. Ese es el loop;
  vive en este doc como checklist operativa cuando haya features en producción.
- **A/B y experimentación — DIFERIDO** hasta tener volumen de uso que dé
  significancia (con 5 usuarios internos, el A/B es charlar con los 5).
  Bayesian bandits: NO APLICA a esta escala.
- **Continuous learning:** ICL (mejorar el contexto con ejemplos reales) =
  ADOPTAR YA (es el nonparametric de arriba); offline retraining = mismo
  DIFERIDO que fine-tuning.

### Seguridad de sistemas agénticos
- **Threat model propio — ADOPTAR YA.** Amenazas en orden real para nosotros:
  (1) prompt injection vía datos — una noticia, un log, una denominación de
  cuenta pueden contener texto que el modelo lea como instrucción; TODO dato
  externo se trata como hostil. (2) Fuga de datos entre roles — resuelto
  estructuralmente (tools por RBAC). (3) Fuga de datos al proveedor —
  regla de oro 3 (anonimización). (4) Abuso de costos — presupuestos Fase 0.
- **Defensas estructurales sobre defensas de prompt — ADOPTAR YA:** tools
  read-only, la jaula SQL del P5 (rol read-only, whitelist, límites), el LLM
  jamás compone comandos/SQL fuera de esa jaula, presupuestos con apagado.
  Un "por favor no hagas X" en el prompt NO es una defensa.
- **Red teaming casero — ADOPTAR YA:** antes de abrir cualquier feature a más
  usuarios, una sesión dedicada a romperla (inyección en datos, preguntas
  fuera de alcance, extracción de datos de otros roles). Los ataques que
  funcionen se vuelven casos del eval set. Threat modeling formal (MAESTRO):
  DIFERIDO hasta exponer IA al público (portal invitado, hoy fuera de alcance).
- **Data provenance — ADOPTAR YA:** todo output de IA queda marcado como tal
  (en UI y en datos: columna `origen`), con su traza. Nunca un texto de IA
  puede confundirse con un dato verificado del sistema.

### UX y colaboración humano-agente
- **Autonomy slider — ADOPTAR YA.** Todo empieza en el nivel más bajo que sirva
  (informar → sugerir → sugerir con 1-click → automático) y solo sube UN nivel
  con evidencia medida. Hoy: todo en informar/sugerir.
- **Comunicar confianza e incertidumbre — ADOPTAR YA:** el copiloto muestra qué
  tools consultó; el triage separa HECHO de HIPÓTESIS; el analista muestra el
  SQL. "No tengo cómo saber eso" es una respuesta válida y preferible.
- **Failing gracefully — ADOPTAR YA:** cada feature define su degradación ANTES
  de construirse (sin proveedor → versión determinista o feature oculta; nunca
  un spinner eterno ni una vista rota).
- **Human-in-the-loop con accountability — ADOPTAR YA:** qué decide la IA y qué
  el humano, definido por proyecto antes de construir; toda acción humana sobre
  sugerencia de IA queda auditada (quién, cuándo, qué sugirió).

---

## Ideas evaluadas y NO elegidas (para no re-proponerlas sin novedad)

News intelligence cruzada con cartera · botón "explicame esto" por vista ·
radar de anomalías estadísticas narradas · extracción de prospectos PDF →
alta de bonos · asistente del portal invitado. Quedaron fuera del alcance
actual por decisión del user (2026-07); varias son extensiones naturales de la
plataforma del Copiloto si algún día se retoman.

## Hecho

- **2026-07-11 — P3 v1: copiloto contextual por vista, CONSTRUIDO** (backend
  `api/services/copiloto.py` + endpoints `/api/ia/copiloto*` + tarea
  `copiloto_vista` + `completar_con_traza` en el gateway + panel
  `ia-vista-panel.tsx` en CEDEARs). Decisión de forma: contextual por tabla, NO
  chatbot global (ver P3). Falta deploy + shadow. De paso: `jobs.triage` sumado
  a `_CRONS_IGNORADOS` del test del Diagnóstico (CI estaba roja desde el P2).
- **2026-07-10 — P2 v1: triage reactivo de incidentes, FUNCIONANDO** (`jobs/triage.py`
  + `ia.triage_incidentes`/`ia.triage_estado` + tarea `triage_incidente` pro + cron
  10 min). Reactivo puro (no batch), 4 guardas de costo (dedup por firma / watermark /
  presupuesto / severidad), lee `logs/<job>.log`, Telegram. Probado con fallas reales
  (ok=✓). Bug encontrado y arreglado vía `ia.trazas`: v4-pro razona → `max_tokens` bajo
  volvía respuesta vacía (700→2500). Falta: shadow + tab OBSERVABILIDAD + thinking mode.
- **2026-07-10 — P1 v2: briefing ampliado** (backend TRD-FX + modal acaquant-web).
  Columnas uniformes HOY·1D·WTD·MTD. Futuros: los 10 de `HOME_FUTUROS` +
  `market_anchors` extendido a futuros con ancla `anchor_wtd` (WTD real). Dólares:
  WTD/MTD al vuelo desde histórico. Bonos que pagan hoy: estructural sobre curvas ×
  held (`acreencias.bonos_pagan_en_fecha`). Diseño medido con diags (ya borrados,
  REGLA #5). Agenda diferida (Finnhub free muerto → FMP/LSEG pendiente de fuente).
- **2026-07-10 — Fase 0.1: gateway `core/ai.py`** (+ tabla `ia.trazas`, migración
  de `ai_resumen` adentro, `scripts/smoke_ai.py`, tests unit). Key DeepSeek
  seteada por el user en el Droplet el mismo día. Smoke OK E2E; IDs
  `deepseek-v4-flash`/`-pro` verificados contra el proveedor.
- **2026-07-10 — Fase 0.2: módulo `ia` en el RBAC** (`a6fd413`): MODULES +
  prefijo `/api/ia` + tests que congelan default-solo-admin e invitado-jamás.
  Tilde de `ia` para `admin` hecho por el user en el panel (canary activo).
- **2026-07-10 — Fase 0.3: observabilidad** (`c7f5308` + web `8e88487`):
  `GET /api/ia/observabilidad` + pill IA en Manager → OBSERVABILIDAD.
- **2026-07-10 — P1 v1 determinista en shadow** (`3010c70` + web `9ea4990`):
  `GET /api/ia/briefing` + modal 10:00 ART en HOME (ver sección P1).
