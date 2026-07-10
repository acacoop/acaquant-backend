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
  - `core/ai_resumen.py`: cliente DeepSeek (requests, `DEEPSEEK_API_KEY`,
    override `AI_RESUMEN_MODEL`) con lectura ejecutiva del control diario —
    DORMIDO hasta que exista la key. Es el embrión de la Fundación.
  - Auto-control de calidad de datos (jobs/controles_datos + tab CONTROLES en
    OBSERVABILIDAD) y playbook determinista de cuarentena ROFEX — la base
    operativa sobre la que se montan Briefing y Triage.
- **Bloqueante único ahora mismo:** crear la cuenta DeepSeek y setear
  `DEEPSEEK_API_KEY` en el Droplet (env del cron + unit de la API). PENDIENTE
  del user.

---

## Fase 0 — Fundación (prerequisito de todo)

**Estado: PENDIENTE**

1. **`core/ai.py` — el gateway único de IA.** Toda llamada a un LLM del sistema
   pasa por acá (ai_resumen se migra adentro). Provee: elección de modelo por
   tarea (flash/pro, thinking on/off), timeouts, reintentos, y **presupuesto**
   (tope de tokens por día global y por usuario; superado → la feature degrada,
   no explota). Proveedor-agnóstico: cambiar de proveedor = tocar un solo módulo.
2. **Módulo `ia` en el RBAC** (MODULES + ENDPOINT_MODULE_PREFIXES + matriz).
3. **Observabilidad de la IA desde el día 1:** cada llamada se registra
   (quién, qué tarea, modelo, tokens in/out, latencia, éxito/fallo) en una
   tabla SQL — es el equivalente de `manager.job_runs` para la IA. Sin esto no
   hay medición, y sin medición no hay mejora (ver Principios). Visible en
   OBSERVABILIDAD.
4. **Suite de evaluación mínima:** por cada tarea de IA, un set chico de casos
   de prueba (input real → output esperado/criterios) que se corre al cambiar
   un prompt o modelo. Empieza siendo un archivo de casos + un script; crece
   con los fallos reales que se vayan encontrando.

---

## Proyectos elegidos (orden = dependencias)

### P1 — Briefing de apertura automático
**Estado: PENDIENTE** · Tipo: pipeline batch + modal in-app · Canal: SOLO
interno (decisión del user 2026-07: NADA de Telegram para esto)

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
**Estado: PENDIENTE** · Tipo: pipeline batch · Canal: Telegram + OBSERVABILIDAD

Sobre job_runs fallados + logs (journalctl) + historial: agrupa fallas por
causa probable, distingue HECHO ("el error dice X, pasó 3 veces") de HIPÓTESIS
("sospecho Y"), y recomienda acción. Acá sí conviene thinking mode / modelo pro.
Loop de mejora incorporado: cuando el mismo patrón aparece repetido, el triage
propone crear un playbook determinista (precedente: cuarentena ROFEX) — la IA
se vuelve innecesaria para lo conocido, que es el objetivo. Nunca ejecuta nada.

### P3 — Copiloto de Mesa
**Estado: PENDIENTE** · Tipo: agente conversacional in-app · Gate: módulo `ia`

Panel de chat en trading.acaquant.com. Arquitectura: LLM + **tools curadas
sobre `api/services`** (leer curvas, AuM, operaciones, comercial…), donde el
set de tools disponible se deriva de los módulos RBAC del usuario — lo que su
rol no ve, no existe como tool (el gate es estructural, no un prompt).
Patrón de orquestación: ReAct simple (razonar → llamar tool → responder);
resistir la tentación de multi-agente hasta que una necesidad real lo pida.
Cada respuesta muestra qué tools consultó (transparencia → confianza) y
comunica incertidumbre ("no tengo tool para eso") en vez de inventar.
Empezar con 5-8 tools de ALTA calidad (descripciones precisas, ejemplos) antes
que 30 mediocres — la curaduría de tools ES el proyecto. Memoria: historial de
conversación por usuario (corto plazo); nada de vector stores hasta que haya
una necesidad concreta. Límite de mensajes/día por usuario.

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

*(mover acá una línea por cada ítem terminado, con fecha y commit)*
