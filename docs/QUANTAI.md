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

Estos son los estándares pro con los que se construye cada pieza — si un
diseño los contradice, se replantea el diseño:

1. **Workflows antes que agentes.** Si la tarea tiene pasos conocidos (briefing,
   prep de reunión), es un workflow determinista con pasos de LLM — más barato,
   predecible y testeable. Agente (el modelo decide qué hacer) solo donde la
   entrada es abierta de verdad (copiloto, analista). Nunca multi-agente sin
   una necesidad que un agente solo no cubra.
2. **Model selection por tarea, no por moda.** Flash para redactar/clasificar;
   pro + thinking para diagnóstico y SQL. Revisar con datos de la tabla de
   observabilidad (costo/latencia/calidad reales), no por sensación.
3. **Tools chicas, puras y bien descriptas.** Cada tool hace una cosa, con
   descripción que dice CUÁNDO usarla, no solo qué hace. Las tools heredan el
   RBAC — el permiso es estructural, jamás un "no muestres esto" en el prompt.
4. **Context engineering explícito.** Lo que el modelo necesita se le da
   curado (definiciones de negocio, reglas, datos del caso); no se le da la
   base entera ni se confía en su memoria de internet. Prompts versionados en
   el repo como código.
5. **Evals como parte del ciclo, no como afterthought.** Cambio de prompt o
   modelo → correr el eval set de esa tarea. Cada fallo real en producción se
   convierte en un caso nuevo del set (el set crece con la realidad, no con
   imaginación).
6. **Observabilidad total.** Toda llamada trazada (tarea, tokens, latencia,
   resultado). El feedback del usuario es señal: botón 👍/👎 en outputs
   interactivos desde la v1 — alimenta el loop de mejora.
7. **Autonomy slider.** Todo empieza en el nivel de autonomía más bajo que
   sirva (informar → sugerir → sugerir con 1-click → automático) y solo sube
   un nivel con evidencia medida de que el anterior funciona. Hoy: todo en
   informar/sugerir.
8. **Failing gracefully.** Cada feature define su degradación ANTES de
   construirse: sin proveedor → versión determinista o feature oculta; nunca
   un spinner eterno ni una vista rota.
9. **Seguridad desde el diseño.** Amenaza principal: prompt injection vía
   datos (una noticia, un log, un nombre de cuenta pueden contener texto
   malicioso que el modelo interprete como instrucción). Defensas: tools
   read-only, el LLM jamás compone SQL/comandos fuera de la jaula del P5,
   presupuestos, y red-teaming casero antes de exponer cualquier cosa a más
   usuarios (probar activamente de romperlo, no asumir buena fe).
10. **Human-in-the-loop con escalamiento claro.** Qué decide la IA, qué decide
    el humano y cómo se escala, definido por proyecto ANTES de construir.
    Accountability: cada acción aplicada por humano sobre sugerencia de IA
    queda auditada (quién, cuándo, qué sugirió la IA).

---

## Ideas evaluadas y NO elegidas (para no re-proponerlas sin novedad)

News intelligence cruzada con cartera · botón "explicame esto" por vista ·
radar de anomalías estadísticas narradas · extracción de prospectos PDF →
alta de bonos · asistente del portal invitado. Quedaron fuera del alcance
actual por decisión del user (2026-07); varias son extensiones naturales de la
plataforma del Copiloto si algún día se retoman.

## Hecho

*(mover acá una línea por cada ítem terminado, con fecha y commit)*
