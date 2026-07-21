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
  desaparece en ~60s, TTL del cache de roles). El rol `invitado`: la decisión
  original era default-deny total; el 2026-07-21 el user decidió habilitarle
  `ia` CON CONDICIONES (congeladas en test): solo los copilotos de vistas de
  MERCADO que ya ve, JAMÁS la guía de la plataforma (mapea el producto entero),
  cada invitado con SU identidad ("guest:<email>" — persiste su propia
  conversación) y SU tope diario bajo (100k default, editable por email en
  Manager).
- **Sin MCP como base.** El copiloto y todo lo interactivo va NATIVO en la app
  (endpoints propios + panel propio). El MCP server existente queda como está
  (conector de claude.ai para RV) pero no es la plataforma de esto.
- **Datos del NEGOCIO hacia el LLM: SOLO a través de la ADUANA (decisión del
  user 2026-07-21 — pisa el descarte del 2026-07-13).** El user habilitó el
  Asistente de Negocio (P7) con esta semántica, innegociable: las IDENTIDADES
  de clientes (nombres, cuentas, documentos) JAMÁS salen al proveedor — la
  aduana `core/pii_gateway.py` las tacha con fichas (CLIENTE_1) antes de
  viajar; las CIFRAS (AuM, P&L) salen pseudonimizadas, atadas a la ficha,
  imposibles de vincular a una persona desde afuera (el diseño que el viejo
  P4 ya había aprobado: "números y placeholders"). El resto de la decisión
  original sigue: nada del negocio va al proveedor SIN tokenizar, y las
  features de mercado no necesitan aduana (datos públicos).
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
- **P7 Asistente de Negocio: M1 construido (2026-07-21)** — ver su sección.
  Pendiente del user: apply_schema + deploy + calibración del matcher + tilde
  del módulo `asistente` en Manager.
- **Cabos sueltos:**
  1. `.env` del Droplet tiene una línea mal formada (warning python-dotenv
     "line 33", sigue apareciendo al 2026-07-14) — no rompe, pero si esa línea
     era una var real se está ignorando en silencio. Ver con
     `sed -n '33p' /root/TradingAV/.env`.
  (El cabo de la frescura del feed MAE a las 10:00 se descartó junto con los
  pendientes del briefing — P1 cerrado 2026-07-14.)

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
   **Presupuestos editables desde el panel (2026-07-11, pedido del user):**
   tabla `ia.config` (precedencia tabla > env > default; cache 60s en el
   gateway) + `GET/POST /api/ia/presupuesto` (editar = SOLO admin, auditado
   quién/cuándo) + editor en la pill IA. Semántica: el GLOBAL diario es techo
   duro del sistema; el tope por usuario no puede superarlo (la suma de
   usuarios sí puede — corta el global). Defaults: 2M global / 1M usuario
   (subido de 200k al medir ~22k tokens/pregunta del copiloto).
4. **Suite de evaluación mínima — EN CURSO (2026-07-11).** Primera tarea con
   eval set: `copiloto_vista` — `evals/copiloto_vista.json` (6 casos: 4 fallos
   reales del shadow + 2 adversos) + runner `scripts/eval_copiloto.py` (corre
   en el Droplet contra datos vivos; checks de presencia por regex, tolerantes
   a la variación del LLM). Contrato: se corre tras CADA cambio al prompt/
   contexto del copiloto; cada fallo real nuevo se suma como caso. Falta:
   sets para `controles_resumen` y `triage_incidente` cuando acumulen fallos
   reales que congelar.

---

## Proyectos elegidos (orden = dependencias)

### P1 — Briefing de apertura — CERRADO COMO ESTÁ (decisión del user 2026-07-14)
> 2026-07-17: única excepción pedida por el user — bloque **CAUCIONES** (TNA ARS
> y USD del plazo vigente, misma fuente que la watchlist:
> `mercado.caucion_snapshot`). `briefing.py::_cauciones` + sección en el modal.
> 2026-07-18: segunda excepción pedida por el user — panel **RESEARCH DEL DÍA**
> al costado de la tabla (el mail de 1816 de HOY desde `ia.research`, texto
> limpio tal cual, scroll propio; si hoy no llegó mail, el panel ni aparece y el
> modal queda como siempre). `briefing.py::_research_hoy` + aside en el modal.
> Cero IA (el copiloto NO consume esta key — lee campos específicos).

La feature queda VIVA tal cual corre (modal 10:00 ART + botón ☀ BRIEFING en el
footer global + `GET /api/ia/briefing` + `api/services/briefing.py`, columnas
HOY·1D·WTD·MTD, determinista sin LLM) pero **no se invierte más en ella**: los
pendientes que tenía (bloque Agenda vía FMP, health de `titulos_sin_flujo`,
frescura del feed MAE a las 10, verificación de "pagan hoy") quedan DESCARTADOS
del roadmap — no re-proponer sin pedido del user. Mantenimiento correctivo
solamente (si se rompe, se arregla). Nota: el mayorista MAE ganó WTD/MTD
anclado en el A3500 el 2026-07-14, ya deployado.

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
tab TRIAGE en OBSERVABILIDAD (hoy solo Telegram) · ~~thinking mode del pro~~
**CABLEADO (2026-07-11):** shape verificado contra la doc (`thinking: {type}`,
default enabled → ahora explícito por tarea: triage enabled, flash disabled;
el razonamiento se guarda en `ia.trazas.razonamiento`) · sumar 'partial'
además de 'error' si hace falta · subir max_tokens de `controles_resumen`.

### P3 — Copiloto de Mesa
**Estado: v1.77 (2026-07-21) — 10 VISTAS ABIERTAS A LA MESA: HOME, Renta Variable,
TRADING (+ el VIGÍA reactivo + memoria de 7 ruedas + modo propositivo de setups),
RENTA FIJA, AGRO, OPCIONES (derivados), ONs,
REUTERS (tablero live de subyacentes US, feed Eikon — ver
`docs/INTEGRACION_REUTERS.md`), RESEARCH (unificada: series 1816 + BCRA + FRED +
reportes + mails de 1816 citables con fecha vía FTS — el "Nivel 2" de
VISTA_RESEARCH.md) y AYUDA (el GUÍA de navegación en toda página — jamás datos)** · Tipo: copiloto contextual por vista · Gate:
`ia` + módulo RBAC de la vista

> **Rollout 2026-07-13:** el user sumó el módulo `ia` a **trader y sales** →
> el copiloto y el briefing dejan el shadow admin-only y quedan disponibles
> para toda la mesa (canary → producción). Se sumó un **cartel de novedad**
> del copiloto (modal centrado una-sola-vez, desde 10:00 ART, global en todas
> las páginas): `acaquant-web/src/components/copiloto-noticia.tsx` — detalle en
> el changelog de `docs/COPILOTO.md`.

> El detalle fino del asistente (qué ve HOY, técnicas, changelog v1→v1.46)
> vive en `docs/COPILOTO.md`. Resumen de lo construido 11-12/07: vista RV
> completa (pulso/rankings/screenings/extremos desde 2005) · vista TRADING
> (8 tarjetas con overrides, libro, tape, movers, posiciones del INTRADAY,
> reloj de mercado con zona muerta 13-16 y doctrina de 3 estrategias) ·
> vista RENTA FIJA (fair value/residuos filtrados a señal real, movimientos
> vs cierre, forwards por z, breakevens+señal vs REM, spread de legislación
> con historia, retorno por curva 7d/14d/MTD, carry+canje, y comparar/
> sensibilidad-por-shocks/descomposición de Estrategia bajo demanda; marco
> PM de breakeven y carry&rolldown) · políticas duras: verificado-o-nada,
> cero aritmética del modelo, solo-señales-reales, guardrails de código con
> auto-corrección (reflexion), voz de operador, tono por rol, chips curados,
> conversaciones separadas · EL VIGÍA: watchers deterministas → toasts
> cero-tokens con "¿lo miramos?" y agregar-tarjeta 1-click. Tooling:
> bateria_rf/bateria_home (preguntas reales, cazaron ~20 bugs) +
> diag_contexto --vista (el contexto exacto sin tokens, todas las vistas). v1.40/41: derivación entre vistas (pregunta de otro
> dominio → "consultalo desde X" + botón RBAC-aware con HANDOFF: la vista
> destino re-pregunta sola, misma conversación), mensajes de presupuesto
> accionables, botón IA al header (ex-TERMINAL) en RV/RF. v1.43: vista HOME
> (watchlist + briefing como contexto, retorno/carry/canje reusados de RF,
> honestidad "describo, no explico causas" — noticias descartadas por el
> user) + 🗣 NARRÁMELO en el modal del briefing (la capa narrativa de P1).
> Pendiente de verificar con rueda abierta: semántica de ventana del
> carry_trade.

> **Doc vivo del asistente: `docs/COPILOTO.md`** — mapa del código, qué ve el
> asistente HOY y **changelog fechado OBLIGATORIO**: todo cambio a contexto/
> prompt/vistas se asienta ahí en el mismo commit (pedido del user 2026-07-11).

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

**v1.2 (2026-07-11) — primer loop del shadow, tal como manda el doc.** La
primera pregunta real ("¿qué acciones hay de IA?") encontró la primera falla:
`es_ia`/`rubro` no estaban en el contexto → el modelo improvisó por nombre de
empresa. Fix: columnas `es_ia` + `rubro` + **zonas de pivots derivadas**
`piv_anual`/`piv_mensual` para TODO el universo (posición del subyacente vs
pivots del período previo; 2 queries agregadas cacheadas 15 min, no 187
llamadas) + la **lectura de pivots de la mesa codificada en el prompt**
(conocimiento no inferible, pedido del user):
- **>R3 / <S3** (anual o mensual) = subió/cayó muchísimo — rompió el mapa del período.
- **R2-R3 / S3-S2** = la tendencia ya está clara.
- **R1-R2 / S2-S1** = subió/cayó algo.
- **alrededor del PP** = zona neutral; el PP es la referencia ideal para tomar decisiones.
El asistente usa esta lectura como contexto implícito (sin listar niveles salvo
que se los pidan). UI: la línea de fuente (📊 vista · filas · hora) salió de la
respuesta — queda el disclaimer fijo del panel (data provenance cubierta).
**Este caso es el candidato #1 del eval set de `copiloto_vista`.**

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

### P4 — Prep de reuniones comerciales — DESCARTADO (2026-07-13, ver "Datos del NEGOCIO" en Decisiones)
**Estado: PENDIENTE** · Tipo: informe con receta (workflow sobre el copiloto) ·
Gate: `ia` + RBAC comercial

"Preparame la reunión con [cuenta]": tenencia, PnL, actividad, acreencias
próximas, estado comercial, + talking points según reglas definidas con el
equipo (ej. cash ocioso → proponer remuneración). Es el copiloto con guion
fijo: 6 llamadas a services predefinidas + redacción. **Privacidad:** los
nombres del cliente se ANONIMIZAN antes de salir al proveedor (CLIENTE_A) y se
re-insertan server-side en el texto final — al proveedor solo llegan números y
placeholders. Éxito medible en reactivación de cuentas DORMIDAS.

### P6 — Memoria de research diario (mail → contexto)
**Estado: INGESTA CONSTRUIDA (2026-07-14) — PENDIENTE credenciales del user +
paso 2 (consumo)** · Tipo: workflow determinista con 1 paso de LLM · Gate: los
consumidores heredan el suyo (copiloto/briefing)

El user recibe un research diario de mercado por mail (macro AR + mundo, estilo
"el día en pocas líneas"). Decisión del user (2026-07-14): ingesta AUTOMÁTICA
por IMAP, nada manual. El objetivo NO es fine-tuning (hechos perecederos no van
a pesos): **la memoria vive en Postgres, no en el modelo** — se persiste y se
inyecta como contexto (nonparametric, ver Principios).

**Paso 1 — ingesta (HECHO 2026-07-14):** `jobs/research_mail.py` (cron */30
10-14 UTC L-V): lee la casilla por IMAP (readonly), filtra por remitente,
persiste el mail CRUDO en `ia.research` (fuente de verdad, citable; dedup por
Message-ID → idempotente) + DESTILADO del LLM `{resumen, temas, hechos}` (tarea
`research_destilar`, flash sin thinking; mail tratado como DATO hostil). LLM
caído → queda `destilado NULL` y el próximo run lo reintenta (el crudo nunca se
pierde). FTS español sobre el cuerpo (ADOPTAR YA: full-text antes que vectores).
**PENDIENTE del user (REGLA #6):** app password de la casilla + remitente →
`RESEARCH_IMAP_USER` / `RESEARCH_IMAP_PASSWORD` / `RESEARCH_MAIL_FROM` en el
`.env` del Droplet (ver docs/SECRETS.md). Probar: `python -m jobs.research_mail
--dry-run`.

**Paso 2 — consumo (SIGUIENTE):** bloque "research de mercado — últimos días
(destilado)" en el contexto del copiloto (vista HOME primero) y del 🗣 NARRÁMELO
del briefing. Después de validar en shadow: ¿el contexto mejora respuestas?
pgvector sigue DIFERIDO — recencia + FTS cubren el uso actual; se activa solo si
aparece la pregunta semántica sobre meses de historia.

### P7 — ASISTENTE DE NEGOCIO (chatbot de jefes) — EN CURSO
**Estado: Milestone 1 CONSTRUIDO (2026-07-21) — pendiente deploy + calibración
del matcher** · Tipo: agente con tools curadas (ReAct simple) · Gate: módulo
RBAC nuevo `asistente` (admin-only default, JAMÁS invitado — REGLA #8)

Chatbot para los jefes: preguntas en lenguaje natural sobre el negocio
(operaciones/cartera/mercado), respuesta en castellano. **La restricción
central**: identidades de clientes JAMÁS salen al proveedor (ver la decisión
de la aduana arriba). Namespace nuevo: `/api/asistente/*` (el viejo
`api/agent` + `/api/chat` está BORRADO y no se recrea).

**Milestone 1 (construido en 5 commits, 2026-07-21):**
1. `core/llm.py` — transporte LLM único provider-agnostic (extraído de
   core/ai.py): el proveedor se nombra en UN archivo; rutear/cambiar =
   tocar solo ese. Invariante congelada por test.
2. `core/pii_gateway.py` — LA ADUANA: tokenize/detokenize con fichas
   estables por chat (CLIENTE_1/CTA_1/DOC_1, mapping en
   `manager.asistente_mappings`, TTL 48h, nunca viaja). 3 capas: catálogo
   real (`clientes.cuentas`+`comitentes`, match exacto/parcial/inicial/
   fuzzy), regex de cuentas/documentos, y enmascarado defensivo de lo que
   PARECE nombre. Fail-closed: sin catálogo, el asistente se niega.
3. `api/services/asistente_tools.py` — tools read-only token-in/token-out:
   `resumen_mesa()` (AuM total + segmentos, agregado sin nombres) y
   `rendimiento_cuenta(ficha)` (resuelve la ficha DENTRO del perímetro).
   El LLM jamás escribe SQL.
4. `POST /api/asistente/chat` (router thin + service
   `api/services/asistente.py`): tokenize → loop de tools del gateway
   (tarea `asistente_negocio`, tier pro, presupuestos/trazas heredados;
   la traza guarda el texto TOKENIZADO = registro auditable de qué salió)
   → detokenize → transcript real en `manager.asistente_chats`. Feature
   flag: sin credencial del transporte, el router NI SE MONTA (patrón MCP).
   Rate limit 10/min·150/día. Invariante testeada: a core.llm no llega
   NUNCA texto sin aduana (mensaje, historial y tools).
5. `scripts/eval_asistente.py` + `evals/asistente.json` — harness de
   regresión con casos de respuesta conocida + NO-LEAK end-to-end en seco
   (0 tokens).

**Calibración del matcher (2026-07-21, MEDIDA con el diag en prod —
catálogo real: 1836 cuentas, 1907 tokens):** el primer smoke/no-leak cazó
dos fallas → fixes estructurales: (1) **corte por frecuencia** (default 5,
`ASISTENTE_TOKEN_MAX_CLIENTES`): un token en >N clientes ('ltda' 97,
'renta' 84, nombres de pila 20-54) no identifica a nadie → fuera del índice,
auto-stoplist basada en datos; (2) **vocabulario de negocio** nunca es
candidato ('total'/'administrado' de la pregunta terminaban tachados como
clientes); (3) **sufijos societarios absorbidos** en la tachadura ('SA'
suelto al lado de la ficha delataba la forma societaria); (4) **fuzzy 0.95**
default (medido: 14% de cruce entre apellidos a 0.90 vs 1% a 0.95).

**Milestone 2 (2026-07-21, mismo día — decisión del user: "es siempre el
mismo asistente"):** NADA de pantalla nueva — el asistente de negocio es la
vista `negocio` del copiloto de siempre (ver COPILOTO.md v1.78): mismo panel
"Consultale a la IA", en las rutas de negocio (operaciones/carteras/back
office/manager) los jefes con módulo `asistente` lo ven y el resto sigue con
el guía. `POST /api/asistente/chat` fue ELIMINADO (una sola puerta:
`/api/ia/copiloto`); el cerebro sigue siendo `api/services/asistente.py`
(aduana + tools + transcript). Evals validados en prod: 2/2 PASS
(aum_total exacto + no-leak e2e con cliente real).

**PENDIENTE (en orden):** tildar el módulo `asistente` para admin en Manager
→ ROLES · shadow del admin unos días desde el panel (cada fallo real → caso
del eval) · más tools (actividad comercial, acreencias próximas) · restore
de conversaciones de negocio en el panel + derivación negocio→guía (anotados
en COPILOTO.md v1.78).

### P5 — Analista ad-hoc de datos — OJO: alcanzado por la decisión "datos del negocio no salen al proveedor" (2026-07-13, MODIFICADA 2026-07-21: ver la decisión de la aduana); el patrón del P7 (jaula + aduana) es la antesala
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
  store aparte — menos infra, misma casa). **2026-07-14: el corpus de research
  llegó (`ia.research`, P6) — igual el v1 va con recencia + FTS; pgvector recién
  si hace falta búsqueda semántica sobre meses de historia.**
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

- **2026-07-11/12 — P3 v1→v1.46: copiloto de mesa COMPLETO en shadow** (4
  vistas: RV, TRADING + vigía reactivo, y RENTA FIJA con marco de portfolio;
  doctrina del trader; verificación estricta con auto-corrección; memoria
  por conversaciones; presupuestos editables con excepciones por usuario;
  saldo real del proveedor; 3 baterías de prueba que cazaron ~15 bugs).
  Historia completa versión a versión: `docs/COPILOTO.md`. Colateral: fix de
  series de precios (backfill 2024+ con auto-reparación de splits) +
  extremos históricos destilados desde 2005.
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
