# AV AGENT — el programa de IA de ACAquant  ⟨DOC ÚNICO · VIVO⟩

> **ESTE ES EL DOC DEL PROGRAMA DE IA. No hay otro roadmap.**
>
> Decisión del user (2026-08-17): *«hay que fusionar, ya que el agente ES el nuevo
> proyecto de IA. No hay que tener muchos documentos y cosas desparramadas: hay
> que unificar todo lo de IA. Va a empezar a pasar desde ahora con el agente; lo
> anterior no funcionó.»*
>
> `QUANTAI.md` queda como **ARCHIVO** de la fase anterior (qué se intentó, qué se
> descartó y por qué — sirve para no re-proponer lo mismo), y `COPILOTO.md` /
> `TOOLS_IA.md` como referencia de lo que sigue corriendo. **El roadmap vivo, las
> decisiones y los principios son este archivo.**
>
> **REGLA.** Doc VIVO con changelog obligatorio: cada etapa completada se marca en
> el MISMO commit, cada decisión se asienta, cada cosa descartada se borra con una
> línea de porqué. Si el doc no refleja el estado real, el trabajo está INCOMPLETO.
>
> Docs que hay que leer antes de tocar esto: `SALUD_CURVAS.md` (el catálogo de
> fallas que el agente reconoce), `RENTA_FIJA.md` §0 (los EJES y el motor).

---

## 0. El programa: qué es y hacia dónde va

### 0.a Por qué el agente ES el programa, y no un proyecto más

El programa anterior nació al revés: **primero el modelo, después el problema.**
Se construyó un gateway, se eligió proveedor, se instrumentaron trazas y se
listaron siete proyectos con LLM — y lo que quedó corriendo fue poco.

El AV Agent nació al derecho y por eso funciona: **un problema real y medible
primero** (222 bonos cuyo dato de mercado nadie audita), **una herramienta que lo
resuelve después**, y el modelo **solo donde agregue algo que una regla no pueda**.
Hoy el agente detecta, razona por ocho lentes, propone un arreglo, lo verifica y
lo aplica — **sin una sola llamada a un LLM**. Esa es la base sobre la que la IA
tiene sentido, no al revés.

### 0.b Los principios (si un diseño los contradice, se replantea el diseño)

1. **El LLM va donde hay ambigüedad y lenguaje. Nunca donde hay aritmética.**
   Comparar `moneda_flujo` contra los ejes es comparar dos strings: un modelo ahí
   sería más lento, más caro y **menos confiable**. Leer un log en prosa y opinar,
   en cambio, es exactamente lo que una regla no puede.
2. **Sin medición no hay autonomía.** *«Un agente que diagnostica sin poder medir
   si acertó no es un agente, es un generador de opiniones.»* La precisión por
   causa (`mercado.av_agent_evals`) es lo que habilita cada paso de autonomía —
   nunca la sensación de que anda bien.
3. **Recompensa VERIFICABLE antes que opinión.** Un arreglo se aplica solo si la
   métrica que disparó el hallazgo vuelve al rango al simularlo. Es un control
   duro, local, gratis y sin depender de terceros.
4. **Agotar lo local antes de salir a la red.** Medido: **38 de 38** hallazgos se
   resuelven con datos que ya están en la base. Una dependencia externa colgada de
   algo que casi no la necesita es lo que dejó la pantalla muda con el 429.
5. **Degradar es distinto de fallar, y "no pude mirar" nunca es "no hay nada".**
   Toda fuente caída apaga SU regla y lo declara; jamás se convierte en un verde.
6. **Un solo lugar decide cada cosa.** Dos implementaciones del mismo criterio
   terminan contradiciéndose — ya costó tres bugs en este agente.
7. **Primero ver, después simular, después escribir.** Ninguna capacidad nueva
   nace escribiendo.

### 0.c Dónde estamos, contra el estado del arte

Mapa contra el programa de Arquitectura de Agentes (UTDT, 2026) — lo que el
agente **ya implementa sin haberlo llamado así**:

| Concepto | Cómo está resuelto acá | Estado |
|---|---|---|
| Autonomy slider | `puede_aplicar` (humano) vs `puede_auto` (robot) | ✅ |
| Confianza e incertidumbre | los **5 estados** del pre-flight | ✅ |
| Failing gracefully | degradación honesta en toda fuente | ✅ |
| Solicitud de guía al usuario | ME PREGUNTA · AVISOS · el dato que se tipea | ✅ |
| Query decomposition | **las 8 lentes** | ✅ |
| Root cause analysis | `analizar()` → causa aguas arriba | ✅ |
| Recompensas verificables | la verificación local del arreglo | ✅ |
| Human-in-the-loop | el humano aprueba toda escritura | ✅ |
| Data provenance / accountability | evidencia congelada + libro de acciones | ✅ |
| Evaluation sets | `mercado.av_agent_evals` | 🟡 tabla + endpoints, **sin UI de voto** |
| Exemplar learning | `av_agent_ignorados` con motivo | 🟡 semilla |
| Memoria / RAG / vector stores | — | ❌ no hace falta todavía |
| Multiagente, swarms, actor frameworks | — | ❌ **no aplica a esta escala** |
| Fine-tuning (SFT/DPO) | — | ❌ sin sentido con decenas de ejemplos |

### 0.d El roadmap, por capas

Cada capa se apoya en la anterior. **Ninguna se saltea.**

| # | Capa | Qué habilita | Estado |
|---|---|---|---|
| 0 | **Control** — parada + tablero de fuentes | poder frenarlo, y ver de qué lee | ✅ 2026-08-18 |
| 1 | **Medición** — eval set con voto humano | saber si acierta | ✅ 2026-08-19 — con UI, juntando |
| 2 | **Cobertura** — SALUD adentro del agente | una sola pregunta: ¿está sano? | ✅ leído y razonado (no escribe) |
| 3 | **Memoria de casos** — exemplar learning | *«esto se parece a PECNO»* | ⬜ |
| 4 | **El LLM donde aporta** — leer prospectos, redactar, agrupar | lo que la regla no puede | ⬜ |
| 5 | **Shadow mode** — propone y registra sin escribir | medir la lane automática | ⬜ |
| 6 | **Canary** — UNA causa se aplica sola | la primera autonomía real | ⬜ |
| 7 | **El patrón a otros dominios** — tesorería, assets | el agente como plantilla | ⬜ |

### 0.e SALUD adentro del agente (2026-08-17)

**Un chequeo de SALUD y un hallazgo del agente son el mismo objeto**: algo que se
evalúa, tiene estado, guarda la evidencia congelada y le pide una decisión a
alguien. Lo único distinto es el sujeto — un bono o un job. Estaban en dos
pantallas, y para contestar *«¿está sano el sistema?»* había que mirar las dos.

Y se complementan justo donde cada uno es débil:

    AV AGENT  →  razonamiento DETERMINISTA (las lentes), y sabe arreglar
    SALUD     →  el HISTORIAL de cada chequeo, y un diagnóstico con IA

Cómo quedó, sin romper nada de lo que ya andaba:

- `detectar_salud()` es **un detector más**: los chequeos que no están en verde
  entran como hallazgos `tipo="salud"`, con la misma forma que el resto. Su
  lectura va en `try` propio — que la observabilidad se caiga no puede tumbar la
  relevada de bonos.
- `av_agent_salud.diagnosticar()` razona un chequeo con **10 lentes**: qué es ·
  ¿corrió cuando debía? · ¿salió bien? · ¿dejó el dato fresco? · **qué dice el
  LOG** · **la FIRMA del error** (lo cruza contra las lecciones ya aprendidas) ·
  ¿ya pasó antes? · qué se rompe aguas abajo · **la lectura con IA** (la única
  que gasta tokens, y va última) · **el comando exacto para volver a correrlo**.
  Las dos del medio y la última son de 2026-08-18: sin ellas el agente decía que
  el job falló pero no POR QUÉ, que es lo único que sirve para decidir.
- **NO escribe nada del lado de SALUD.** SALUD sigue siendo el dueño de su
  estado: si el agente escribiera el suyo habría dos verdades sobre si el sistema
  está sano, que es el problema que la fusión vino a eliminar.
- `ACCION_POR_TIPO["salud"] = "salud"` — una **puerta de SOLO LECTURA**
  (2026-08-18). Nació en `None`, que escondía el botón: el agente razonaba el
  chequeo y no había forma de pedírselo desde la pantalla. Ahora la puerta
  existe y **no escribe**: el modal fuerza `puedeAplicar = false`, y no pinta
  «BLOQUEADO» porque no es que el agente frenó algo — es que todavía no escribe
  de ese lado. Relanzar un job tiene efectos afuera de `mercado.curvas` y se
  habilita cuando el eval set diga que acierta.

**Los pasos y el veredicto se IMPORTAN de la puerta de bonos, no se copian.** Que
SALUD y un bono se vean IGUAL en el modal no es estética: es lo que permite que
una sola cabeza lea las dos cosas, y lo que evita que dos pantallas digan cosas
distintas con los mismos nombres.

### 0.h EL TABLERO DE CONTROL (2026-08-18)

Pedido del user: *«quiero control total del agente desde el modal por las
dudas»*. El punto de partida, medido: **el agente tenía 15 endpoints y los 15
actuaban sobre UN hallazgo.** Ninguno actuaba sobre EL AGENTE — si empezaba a
escribir algo mal, la única forma de frenarlo era no apretar el botón, o apagar
la API entera.

**La parada** (`mercado.av_agent_control`) corta las escrituras de datos de
mercado —alta, flujos, arreglo, crear curva— y **deja intacta la lectura**.
Frenar diagnosticando es lo que uno quiere mientras investiga: si la parada
apagara todo, el primer reflejo ante una duda sería quedarse sin la herramienta.
Ignorar un ticker y cerrar un aviso siguen andando: son la anotación de una
decisión humana, no un dato de mercado. **El alcance está escrito en la
pantalla** — una parada ambigua es peor que ninguna.

Tres cosas la hacen real y no un botón que miente:

- **El guardia se consulta ANTES de simular.** Simular gasta créditos de 1816 y
  gastarlos para después rechazar la escritura sería tirar el recurso.
- **Un test recorre toda función `aplicar*` y exige que llame al guardia.** Una
  puerta nueva que se lo olvide no rompe ningún test obvio: escribe con el
  agente frenado, en silencio. Mismo patrón que `core/instrumentos_validos`
  aplicado en el único punto por el que pasan todas las suscripciones.
- **Si la base no contesta, se usa el último valor conocido.** Sin eso una
  parada activa se evaporaba ante un blip de red — el interruptor mentía justo
  cuando el sistema está peor. Sin haber leído nunca **se permite escribir**, y
  es una decisión: la parada no es un control de seguridad (eso lo dan
  `require_admin` y el humano que aprueba), y fallar cerrado rompería el agente
  por un problema de la MISMA base donde escribe.

**Las fuentes** — Postgres (RTT medido), 1816 (token y cuota), catálogo local,
precios live, última relevada — cada una con su estado **y para qué sirve**.
Es la generalización del bug del briefing del mismo día: una fuente degradada se
notaba recién al leer un resultado raro, y para entonces uno debuggea el
resultado en vez de la fuente. **Ninguna lectura pega a la red**: un tablero que
gasta un crédito cada vez que se mira consume el recurso que vino a cuidar.

### 0.i EL DIAGNÓSTICO MASIVO (2026-08-18)

Pedido del user: *«un botón que haga un estado de situación con los que dieron
error, los que dieron bien, etc., bien completo, que quede para copiar y pegar»*.

**Por qué vale más de lo que parece.** Los diagnósticos se miran de a uno, y de a
uno **no se ven los patrones**. Un informe de 68 muestra lo que ninguna fila
individual puede: que 68 casos son 4 causas (el trabajo real es más chico de lo
que parece), que la verificación de una causa NO vuelve al rango en 12 de ellos
(entonces esa causa está mal), o que 9 fallan con la MISMA excepción — eso es UN
bug de código disfrazado de 9 hallazgos. El bug de `moneda_flujo` se encontró
así, comparando 8 hallazgos contra 30 bonos.

**Corre en BACKGROUND, y no es una preferencia.** El plan de 1816 permite **1
petición por segundo** y el throttle es global entre procesos: 68 bonos con
`cashflow` son ~82 s de piso y 2-3 minutos reales. Ningún request HTTP sobrevive
a eso — el propio agente aborta a los 45 s por `_PRESUPUESTO_S`. El token
compartido y el throttle **se heredan**: todo pasa por `mercado_1816._get`, así
que una corrida masiva no consume ni un login.

Decisiones que lo hacen servir:

- **No se re-implementa ningún diagnóstico**: cada caso pasa por la MISMA puerta
  que el modal, y hay un test que lo exige.
- **Los casos los manda el front**, porque son los que el usuario ve con su
  filtro puesto. Re-derivar el filtro en el backend sería una segunda
  implementación del mismo criterio.
- **Una excepción en un caso no tumba la corrida**, y los que explotan van
  ARRIBA del informe: son bugs del agente, no bonos mal cargados.
- **El informe llega parcial** — se puede mirar mientras corre, que es lo que
  deja abortar una corrida que ya se ve mal.
- **`interrumpido` se deduce de `latido_at`**: si el proceso muere no puede
  dejar constancia, y mostrar «corriendo» para siempre sería mentir.
- **El resumen se deriva del detalle** en la lectura; persistirlo lo dejaría
  contradiciendo a sus propios casos.
- **Tope de créditos** (5.000), medido contra `/balance` y no estimado. No es
  para ahorrar —el plan da 100.000 y usamos ~4.000— sino para que un bug que
  pida `cashflow` en loop se corte ahí. El límite de 500 casos es por RELOJ.

Lo que sigue (el orden acordado con el user 2026-08-18): corregir la causa desde
el modal ⇒ llena el eval set solo · los sliders de autonomía por causa ·
deshacer + lote · calibración, re-chequeo a 24 h y el DAG de lentes.

### 0.j LO QUE EL AGENTE SABE HACER — las ACCIONES (2026-08-19)

Pedido del user, con los ejemplos puestos por él: *«ya sabés exacto qué campo de
qué función hay que modificar… que el mismo agent aprenda a sugerir y que, si le
das OK, actualice en el momento y luego controle que lo hizo bien en el mismo
proceso. Ej.: OTC TRI DLR son siempre derivados; el X29E7 tiene la palabra CER en
el nombre… que ya tenga la feature armada pero que se haga con mi permiso, o que
yo escriba lo que tiene que hacer y él lo haga»*.

Hasta acá el agente **diagnosticaba y mandaba a otra pantalla**. Con 68 casos eso
no es ayuda: es una lista de tareas con un link al lado. La diferencia entre
detectar y resolver es esta capa.

**EL CICLO, Y ES UNO SOLO PARA TODO:**

    PROPONER  →  (el humano da OK)  →  APLICAR  →  VERIFICAR

Es el MISMO ciclo del alta de un bono (simular → aplicar → verificar). Que sea
uno solo no es prolijidad: el que aprueba un cambio de cartera no tiene que
aprender otro modelo mental que el que aprueba un alta, y el día que se le dé
autonomía a una acción se le da con el mismo interruptor.

**Las cuatro decisiones que sostienen el diseño:**

1. **La regla determinista SIEMPRE primero; el modelo solo para lo que quedó
   afuera.** El 80% de estos casos los resuelve una regla de tres líneas
   (`OTC`/`TRI.`/`DLR`+fecha → DERIVADOS, `CER` → ARS, código CAFCI → FCI), y
   gastar tokens *y atención humana* en eso es tirar los dos recursos que
   escasean. Cada propuesta viaja con su `fuente` (`regla` ∣ `ia`) porque
   **cambia cuánto hay que mirarla**: una regla se audita leyendo el código una
   vez; una sugerencia del modelo hay que mirarla caso por caso.

2. **El modelo elige de una LISTA CERRADA, nunca escribe libre.** «Derivados» es
   plausible y está mal escrito, y eso rompe los filtros que comparan exacto —el
   divisor del AuM, Tenencia Valorizada— **sin dar ningún error**. Lo que no está
   en la lista se descarta antes de llegar a la base, y el modelo tampoco puede
   contestar sobre un sujeto que nadie le pasó.

3. **La escritura va por la MISMA puerta que usa la pantalla**
   (`assets_sql.set_campos`, `contrapartes_seg.add_contraparte`). Un segundo
   camino de escritura termina con dos criterios distintos para el mismo dato:
   es así como se llega a que la mitad de las carteras tengan un espacio al
   final. Por eso `_write_sql` se **movió del router al service** en este mismo
   cambio — un service importando de un router era la señal de que la puerta
   estaba en el lugar equivocado.

4. **Aplicar y verificar son UN paso, y `verificado` decide el estado.** Después
   de escribir se **relee de la base**; si la lectura no confirma, la propuesta
   queda **`fallida`**, jamás `aplicada`. Marcar hecho algo que no se puede
   comprobar es exactamente cómo un tablero termina en verde con el dato roto —
   el incidente que dio origen a SALUD. Y cada propuesta se aplica **sola**: una
   que falla no arrastra a las otras, porque aprobar diez y que se caigan las
   diez por la séptima es la forma más rápida de que nadie vuelva a apretar el
   botón.

**Proponer NO trabaja sobre la foto del cron: vuelve a correr el control.**
*«Todo lo que figura en encontró tiene que ser porque realmente está y sigue
pasando»* (user). Sugerir un arreglo para un caso que ya se resolvió es el
cementerio de avisos viejos que este rediseño vino a eliminar.

**El humano puede CORREGIR el valor antes de aplicar.** Sin eso, ante una
sugerencia casi buena solo queda descartarla e ir a Manager a mano —todo el
trabajo del agente a la basura por una letra— y las propuestas que nacen sin
valor (el ping: a quién avisarle) serían inaplicables.

**Rechazar también se registra.** Una propuesta descartada mide tanto como una
aplicada: es lo que dice que el agente sugirió algo que un humano no compró. Sin
eso parecería tener 100% de acierto para siempre. **Cada propuesta con su
resultado ES el eval set de las acciones** (§0.f), y el `acierto` se calcula
sobre lo DECIDIDO —no sobre lo pendiente, que todavía no dijo nada—; sin nada
decidido vale `None` y no `0`, porque «cero acierto» y «todavía no sabemos» son
cosas distintas y la primera frenaría la autonomía por una medición que nunca se
hizo.

**Las cuatro acciones de arranque** (`api/services/av_agent_hacer.py`):

| acción | control | qué hace | escribe por |
|---|---|---|---|
| `assets.cartera` | `assets_sin_cartera` | la CARTERA por patrón del nombre; lo que la regla no sabe va al modelo con la lista de 8 carteras | `assets_sql.set_campos` |
| `assets.fci` | `fci_incompletos` | copia el EMISOR de **otra clase del mismo fondo** (mismo código CAFCI) | `assets_sql.set_campos` |
| `contrapartes.alta` | `contrapartes_pendientes` | da de alta la contraparte **que el conciliador ya sugiere** | `contrapartes_seg.add_contraparte` |
| `avisar.responsable` | `comitentes_sin_nivel1` | **le avisa a una persona** de la plataforma | `av_agent_vista.avisar_a` |

- **`assets.fci` NO adivina: copia de un hermano.** El código CAFCI es la
  identidad del fondo y la clase es la variante, así que copiar no es una
  inferencia, es un hecho (la misma idea de la regla `herencia` de
  `assets_autofill`). **Si dos hermanos no coinciden, no se propone**: dos
  emisores distintos para el mismo CAFCI es un dato ROTO, no una ambigüedad que
  se resuelva eligiendo uno.
- **`contrapartes.alta` no parsea el texto del control.** El detalle guardado es
  de la última corrida del cron; se vuelve a llamar a `reconciliar()` —la MISMA
  función que usa el control y el botón de Manager— y se propone sobre lo que
  dice HOY. Parsear una frase habría atado el alta al formato de un string.
- **`avisar.responsable` NO crea una tabla de notificaciones nueva.** Ya existe
  la lista de pendientes del agente (`mercado.av_agent_avisos`): tiene alta,
  cierre por una persona y pantalla. Solo le faltaba **a quién** → columna
  `para` (NULL = de todos, que es como venía funcionando). Un segundo buzón daría
  dos lugares donde mirar lo que hay para hacer — el problema exacto que SALUD
  vino a resolver cuando la observabilidad estaba en seis pantallas. Es **UN
  aviso por control y no uno por caso**: 200 pings de «esta cuenta no tiene
  nivel_1» no son 200 avisos, son un aviso ignorado. Y el destinatario **lo
  elige el humano**: a quién le toca un tema interno no es algo que el nombre del
  caso pueda decir.

**Agregar una acción nueva es una clase con tres métodos y una línea en
`ACCIONES`.** Ni endpoint, ni tabla, ni UI: la acción es un *parámetro* de los
tres endpoints, y `POR_CONTROL` se deriva del registro, así que la lente de SALUD
la ofrece sola. Eso es lo que pidió el user cuando dijo *«la arquitectura tiene
que ser escalable porque va a ser un montón de cosas»*.

**El interruptor del modelo vive en `_proponer_con_ia` y no adentro de cada
acción** (un `ContextVar`, para que dos requests en paralelo no se pisen). Si
cada acción tuviera que acordarse de mirar el flag, la que se olvide gasta tokens
con el modelo apagado y nadie se entera hasta ver la factura.

**Dónde se ve.** Es una lente más del diagnóstico de SALUD —«Esto lo sé hacer · N
casos»— y **no se dibuja si el control no tiene acción**: un renglón que dice
«para esto todavía no sé hacer nada» aparece nueve veces y entierra las dos que
sí. La lente **declara la capacidad, no propone**: proponer cuesta (una corrida
del control, a veces una llamada al modelo) y no tiene sentido pagarlo cada vez
que alguien abre un diagnóstico a mirar.

- Tablas: `mercado.av_agent_propuestas` (UNIQUE `accion+sujeto+campo` → re-proponer
  ACTUALIZA en vez de acumular diez para el mismo asset) y la columna `para` de
  `mercado.av_agent_avisos`.
- Endpoints (admin-only, `api/routers/ia.py`): `GET /av-agent/hacer`,
  `POST /av-agent/hacer/proponer`, `/aplicar`, `/rechazar`, y
  `GET /av-agent/mis-avisos` (SIN `require_admin` a propósito: el destinatario de
  un ping no necesariamente administra nada, y filtra por su propio email — no
  hay forma de pedir los de otro).
- Tarea de IA: `av_agent_accion` (tier pro, thinking **disabled** — no es
  análisis de patrones, es clasificar contra una lista cerrada).
- 31 tests en `tests/unit/test_av_agent_hacer.py`.

**Lo que queda pendiente de esta capa**: deshacer (el `antes` ya se guarda, falta
el botón), instrucción en texto libre (*«que yo escriba lo que tiene que hacer y
él lo haga»*), y la lane automática por acción cuando el acierto medido lo
habilite — nunca antes, y siempre prendida a mano.

### 0.k EL COPILOTO SE DIO DE BAJA — y qué dejó (2026-08-19)

**Decisión del user:** *«desactivar el CONSULTALE A LA IA de todas las vistas,
eliminarlo del backend y toda documentación de la misma… este proyecto sirvió
como inicial pero no cumplió con la necesidad, por lo que de momento se elimina
dando paso a AV AGENT, que va a ser una versión superior»*.

**Qué era.** Un botón ✧ CONSULTALE A LA IA en cada vista de mercado (home, renta
fija, renta variable, agro, derivados, trading, research, reuters) más el
ASISTENTE DE NEGOCIO para los jefes y el GUÍA de la plataforma. Le pasabas los
datos de la vista y contestaba preguntas sobre ellos. También el VIGÍA de
/trading (toasts cuando una tarjeta tocaba un nivel) y la NAVEGACIÓN ASISTIDA
(el guía te dejaba los filtros puestos).

**Por qué no alcanzó, y esto es lo que importa que quede escrito:**

Un copiloto **contesta lo que le preguntás**. Eso tiene dos techos que no se
arreglan con mejores prompts:

1. **Requiere que ya sepas qué preguntar.** El backfill de tenencias falló dos
   días y nadie se enteró — nadie iba a preguntarle al copiloto «¿corrió el
   backfill?», porque el problema es justamente que no sabías que había un
   problema. El valor no estaba en responder: estaba en **avisar**.
2. **No deja nada.** Cada conversación empezaba de cero y terminaba en la
   pantalla. No producía un cambio en el sistema, ni un registro de si acertó, ni
   una decisión que alguien pudiera aprobar. Se gastaban tokens en producir
   texto, y el texto se cerraba con la pestaña.

El AV AGENT invierte las dos cosas: **empieza él** (detecta, releva, vigila) y
**termina en un cambio verificado** (propone → das OK → escribe → relee). Por eso
no es «el copiloto mejorado»: es otra forma de usar el modelo, donde el LLM ocupa
el lugar chico —desambiguar, clasificar, leer patrones— y el grueso lo hacen
funciones deterministas que se pueden auditar leyendo el código una vez.

**Lo que se rescata y sigue vivo** (no todo fue a la basura):

- **El gateway `core/ai.py`** — registro de tareas, presupuestos, trazas,
  ruteo por proveedor y la invariante de privacidad. Lo usa el agente entero.
- **Las lecciones de tokens**, pagadas ahí: el razonamiento cuenta como output
  (un `max_tokens` corto devuelve respuesta VACÍA), y `thinking` va apagado
  cuando la tarea es clasificar y no razonar.
- **`ia.trazas`** — toda llamada al modelo queda registrada con tokens, latencia
  y resultado. Es lo que permitió medir esto en vez de opinarlo.
- **La postura de privacidad**: una tarea que ve datos del negocio no puede
  correr en un proveedor que entrena con ellos, y el gateway **se niega** en vez
  de confiar en un string.

**Lo que se eliminó junto con él** (regla nueva del user: *«si lo usa AV Agent
perfecto, si no se elimina»* — nada de IA corriendo por atrás sin lector):

| qué | por qué se fue |
|---|---|
| `copiloto_vista` · `copiloto_vista_pro` · `asistente_negocio` | eran el copiloto |
| `critico_calidad` (`jobs/ia_calidad`, 21:30 diario) | evaluaba conversaciones del copiloto: sin copiloto, sin objeto |
| `triage_incidente` (`jobs/triage`, **cada 10 minutos**) | escribía en `ia.triage_incidentes` y **NADIE la leía** — ni un endpoint ni una pantalla |
| `controles_resumen` (1 llamada/día) | su texto terminaba en un `print()` del log del job |
| `salud_diagnostico` | lo generaba el panel de SALUD; sin panel nadie lo genera |
| `core/pii_gateway.py` (la aduana PII) | existía solo para el asistente de negocio |
| el VIGÍA de /trading y la NAVEGACIÓN ASISTIDA | colgaban de endpoints del copiloto |

De **11 tareas de IA registradas quedan 4**: `av_agent_informe`,
`av_agent_accion`, `research_destilar` (ingesta del mail de 1816, se lee todos
los días en pantalla) y `smoke`.

⚠️ **La regla que queda, y que hay que aplicar antes de sumar una tarea nueva:
¿QUIÉN MIRA SU SALIDA?** Si la respuesta es «queda en una tabla», la tarea no va.
`triage_incidente` corrió cada diez minutos durante semanas contra una tabla que
nadie abrió nunca, y no se notó porque **funcionaba**: no fallaba, no daba error,
solo gastaba. Eso es más difícil de detectar que un bug.

**Las tablas NO se dropearon** (`ia.calidad_flags`, `ia.triage_incidentes`,
`manager.asistente_chats`, `manager.asistente_mappings`,
`manager.salud_diagnosticos`): borrar código es reversible con un `git revert`,
borrar datos no. Quedan huérfanas y se limpian cuando el user lo decida.

### 0.l SALUD sale del front y queda SOLO adentro del agente (2026-08-19)

*«Eliminar SALUD del front de observabilidad… toda la salud, y esto pasa 100% por
el agent»* (user, mismo día).

**El motor no se tocó.** `api/services/salud.py` sigue siendo el dueño de la
evaluación —los chequeos, los contratos de frescura, los eventos, el historial— y
el agente lo LEE. Lo que se dio de baja es la **segunda pantalla**:

- la pill **SALUD** de Manager → OBSERVABILIDAD y su panel;
- el **botón** de la barra inferior (al lado de BRIEFING);
- el **modal de alertas** que interrumpía;
- los seis endpoints `/api/manager/salud*`, que solo esos componentes usaban.

**Por qué está bien:** SALUD nació para unificar la observabilidad que estaba en
seis pantallas. Que el agente diagnostique un chequeo con ocho lentes, lo
re-controle en el momento y sepa arreglarlo (§0.j) mientras SALUD lo mostraba
aparte era **volver a tener dos verdades sobre el mismo estado** — el problema
original, repetido un nivel más arriba.

**Lo que NO se perdió, porque era la razón de existir de SALUD:** la
interrupción. El incidente que lo originó (el backfill que falló dos días y nadie
se enteró) enseñó que **la señal tiene que buscar al admin**, no esperarlo en una
pantalla que hay que ir a abrir. Eso se movió al agente con sus reglas intactas:

- abre **solo ante una transición NUEVA a problema y sin ver** por ESE admin;
- que algo **se arregle nunca abre nada** (lo filtra el backend);
- **nunca en intervalo fijo** — un modal que repite lo mismo se cierra sin leer;
- se puede **silenciar** un chequeo, y silenciarlo **no lo esconde**: sigue en la
  lista con su estado real. Un chequeo que desaparece al silenciarlo es un
  problema que se te olvida.

Endpoints nuevos, todos bajo el agente: `GET /av-agent/salud/pendientes`,
`POST /av-agent/salud/vistos`, `POST /av-agent/salud/silenciar`.

**De yapa, dos cosas que la mudanza destapó:**

- **Un job con dos líneas de cron aparecía DOS VECES** en la lista (`av_agent_live`
  era dos filas idénticas — el user lo marcó en pantalla). El id es `job:<label>`,
  así que dos crons del mismo job colisionaban. Ahora se colapsan en uno, quedando
  el estado **menos** alarmante: dos crons son dos ventanas del mismo job, así que
  haber corrido en cualquiera significa que corrió.
- **El módulo RBAC `asistente` se eliminó**: sin asistente de negocio era un
  checkbox en ROLES Y PERMISOS que no controlaba nada.

**Qué queda de IA para el portal invitado** (REGLA #8): el copiloto era la razón
por la que el invitado tenía el módulo `ia`. Hoy lo único no-admin bajo `/api/ia`
es el **BRIEFING**, que es dato de mercado y por lo tanto exactamente lo que ese
portal existe para mostrar. Hay un test que lo congela: **cualquier endpoint nuevo
de `/api/ia` sin `require_admin` lo hace fallar**, así el AV AGENT —que habla del
estado interno del sistema— no puede quedar alcanzable por www sin que nadie lo
note.

> **El BRIEFING se queda y NO es IA.** El user preguntó por las dudas: verificado,
> `api/services/briefing.py` no tiene una sola llamada al modelo. Futuros US,
> dólar oficial (MAE live + A3500) y cierres MEP/CCL con sus variaciones, todo
> calculado. Nunca gastó un token.

### 0.m LO QUE SABE EXPLICAR — VALIDACIONES entra al agente (2026-08-19)

**Pedido del user**, mirando Manager → VALIDACIONES:

> *«Cada una de las validaciones que hay acá tiene que ser funcionalidades que el
> agent domine a la perfección, porque son cosas que sabría hacer un trader o
> alguien de finanzas… sacándole la palabra DEBUG. Es decir: che, saber por qué
> esta TEA rinde tanto, por qué la TNA de futuros es tanto, saber las breakevens,
> saber tasa fija… quiero ir migrando funciones útiles al agent para que el día
> de mañana le hable y se lo pida.»*

**EL HALLAZGO, y es lo que vale de todo esto:** esas ocho pantallas **nunca
fueron herramientas de debug**. Son las preguntas que se hace alguien de finanzas
todos los días, escritas con nombre de programador. Lo único que las hacía
parecer internas era el nombre y el lugar:

| se llamaba | es |
|---|---|
| Debug TEA Curvas | **¿Por qué este bono rinde lo que rinde?** |
| Debug TNA Futuros DLR | **¿Por qué el futuro de dólar paga esa tasa?** |
| Debug Soberano | **¿Cómo se arma el rendimiento de un soberano?** |
| Debug Breakevens | **¿Qué inflación está descontando el mercado?** |
| Debug Pivot Points | **¿De dónde salen los niveles de soporte y resistencia?** |

Viven en `api/services/av_agent_explicar.py`, se ven en la tab **SABE** del modal,
y **Manager → VALIDACIONES queda donde está** (el user: *«no digo que lo borres
de acá, pero sí migrarlo»*).

**POR QUÉ ESTO ES EL PASO PREVIO A «QUE SE DÉ CUENTA SOLO»**

El user también dijo hacia dónde va: *«que en algún momento ya ni sea necesario,
que sea un sistema entero que se dé cuenta al toque que algo raro pasa… que ni
haga falta decirle que hay algo mal»*.

**El que sabe EXPLICAR un número sabe JUZGARLO.** Un explicador que reproduce el
cálculo paso a paso termina con el número recalculado al lado del persistido; si
no coinciden, eso ya no es un detalle de la explicación: **es un hallazgo**. Por
eso cada explicador devuelve un campo **`discrepancia`** aparte de los pasos, y
ese campo es el puente entre «me preguntaste» y «te aviso». Hoy lo usa
`¿por qué rinde?` (recalculado vs. lo que muestra la app, umbral 50 bps); el
camino es que cada explicador que se sume traiga el suyo.

**LAS CUATRO DECISIONES**

1. **No se reimplementa ningún cálculo.** Cada explicador **envuelve** la MISMA
   función que ya usa la pantalla (`debug_curva.debug_calculo_tea`,
   `debug_derivados.debug_soberano`/`breakevens_debug`/`debug_tna_futuros`,
   `quant.pivot_points.debug_4_timeframes`). Es la misma regla que gobierna las
   ACCIONES: una sola puerta. Dos implementaciones del mismo cálculo terminan
   dando dos respuestas a la misma pregunta, y la pantalla y el agente
   contradiciéndose. **Congelado por test.**
2. **El cálculo determinista produce los NÚMEROS; el modelo produce la FRASE.**
   Nunca al revés. El modelo no calcula una TEA ni infiere un flujo: recibe los
   números que ya salieron y los convierte en la línea que el humano quería leer.
   El prompt se lo prohíbe explícito (*«usá SOLO los números que te paso, no
   calcules ni infieras»*) y hay un test que exige esa instrucción — sin ella, un
   modelo servicial completa el dato que falta.
3. **Sin modelo, la explicación sale igual.** La frase es lo último y lo más
   chico. Una respuesta que depende del modelo para existir es una respuesta que
   un día no está (sin credencial, sin presupuesto, proveedor caído).
4. **La frase NO puede tapar la discrepancia.** Viajan en campos distintos y se
   dibujan separadas — la frase como texto, la discrepancia en rojo. Una es una
   explicación; la otra es un aviso.

**LOS QUE NO ERAN PREGUNTAS SINO PROBLEMAS.** Dos de las ocho no explicaban nada:
avisaban. Esos no van a la tab SABE — **van a donde no haga falta apretar un
botón**:

- **«Títulos sin flujo»** pasa a ser el control `titulos_sin_flujo`
  (`jobs/controles_datos.py`). Así se re-verifica solo todos los días, entra al
  agente con su historial, y lo que se resuelve desaparece sin que nadie lo
  marque. **Solo los que están EN CARTERA**: un bono sin flujo que no tenemos no
  cuesta nada hoy; uno que tenemos **no valúa**, y eso sí es plata mal contada.
  Filtrar es lo que separa un aviso de un catálogo de 300 filas que nadie mira.
- **«Check Tasa Fija»** y **«Backfill Tasas»** quedan pendientes: el primero es
  otro control, el segundo una ACCIÓN de §0.j (escribe TEA/TEM).

### 0.n LA TAB IA DE OBSERVABILIDAD SE ELIMINA (2026-08-19)

*«¿Qué sentido tiene toda esta parte de IA ahora? Más allá de saber el crédito
disponible —que tampoco es relevante— el resto ocupa espacio nada más… estoy
haciendo limpieza de cosas que no servían para absolutamente nada y no tenían uso
alguno»* (user).

Tenía razón, y el argumento es el mismo que fundó SALUD: **el historial de 874
llamadas no se abrió nunca, y un tablero que hay que ir a abrir es un tablero que
no se abre.**

Pero de ese panel había DOS números que sí importan — no porque sean lindos de
ver, sino porque **si se rompen nadie se entera**:

- el **GASTO**: un bug que llame al modelo en loop quema el presupuesto del día
  en minutos, y el síntoma es que la IA deja de contestar «sin razón»;
- los **ERRORES**: llamadas que fallan calladas dejan al agente mudo con la
  pantalla en verde — el modo de falla exacto que SALUD existe para cazar.

Así que se invierte: en vez de una pantalla que hay que visitar, **un chequeo que
te busca**. `salud._chequeo_ia` → `ia:gateway`, verde mientras el gasto y los
errores estén en rango. Umbral del gasto en **80% y no en 100**: avisar cuando ya
no queda presupuesto no sirve — para entonces la IA ya está caída.

`ia.trazas` **se sigue escribiendo**: es el registro auditable de toda llamada al
modelo y el insumo del día que el agente aprenda de sí mismo. Lo que se eliminó
es la pantalla, no el dato. Para mirarlas a mano quedó
`python -m scripts.diag_ia_trazas`.

> **Sobre «que se retroalimente solo»** (user: *«todo acá en AV AGENT se tiene que
> retroalimentar de manera automática por el propio LLM, tiene que aprender de sí
> mismo»*): la materia prima ya se está juntando y son TRES ledgers, no uno —
> `mercado.av_agent_evals` (el ✔/✖ humano por diagnóstico),
> `mercado.av_agent_propuestas` (cada sugerencia con si se aplicó, se rechazó o
> falló) y `ia.trazas`. **Todavía no se cierra el loop a propósito**: con la
> cantidad de votos de hoy, cualquier ajuste automático estaría calibrando sobre
> ruido. El disparador es el mismo de siempre — `MIN_VOTOS` (§0.f) —, no una
> fecha.

### 0.o LA TAB SKILLS — el registro único, y la LEY (2026-08-19)

**LA LEY, en palabras del user:**

> *«Necesito que se vaya centralizando todo: no solo esto, también lo que sabe
> resolver, lo que va entendiendo cuando encuentra algo… va a haber distintos
> tipos de habilidad pero POR LEY Y REGLA todo lo nuevo que se agregue de
> funcionalidad o habilidad tiene que quedar en esta tab, para que se vaya
> mapeando todo lo que va consolidando. Y a su vez dejar asentado si esa skill
> usa para algo IA o no, ya que muchas es solamente una función.»*

**Por qué se DERIVA y no se escribe a mano.** Una lista de capacidades mantenida
a mano se queda vieja **la primera vez que alguien tiene apuro**, y una lista
desactualizada es peor que no tenerla: dice que el agente sabe algo que no sabe,
o esconde algo que sí. Este proyecto ya pagó ese error dos veces —por eso §0 de
`MAPA_APP.md` y `deploy/SISTEMA.md` se autogeneran—. Así que
`api/services/av_agent_skills.py` **arma el catálogo leyendo los registros que ya
existen**:

    DETECTAR   av_agent.ACCION_POR_TIPO + jobs.controles_datos.CONTROLES
    EXPLICAR   av_agent_explicar.EXPLICADORES
    RESOLVER   av_agent_hacer.ACCIONES

Una skill nueva aparece **sola, por existir**. No hay forma de agregar una
capacidad y olvidarse de mapearla, porque no hay nada que acordarse de hacer.

**Y donde el catálogo no puede derivar, hay un test que exige la descripción.**
Los detectores son funciones sueltas sin metadatos, así que su frase va a mano en
`_QUE_DETECTA` — y `test_todo_detector_nuevo_tiene_que_describirse` falla si
alguien suma uno y no lo describe. También falla al revés
(`test_no_se_describen_detectores_que_no_existen`): decir que el agente sabe algo
que ya no hace es peor que no decir nada. **Esa es la ley: no una convención, un
test.**

**El orden de las tres secciones es el orden en que crece el agente:**

    darse cuenta solo  →  poder explicarlo  →  saber arreglarlo

**POR QUÉ IMPORTA DECIR SI USA IA — y por qué son TRES valores, no un booleano.**
Lo pidió el user explícito, y no es una curiosidad técnica: **cambia cuánto hay
que desconfiar**. Una skill determinista da el mismo resultado siempre y se
audita leyendo el código una vez; una que pasa por el modelo hay que mirarla caso
por caso. Y la categoría más común acá es la tercera, la que se suele contar mal
para los dos lados:

| valor | qué significa |
|---|---|
| `no` | es una función. El modelo no participa. |
| `opcional` | la parte que resuelve es determinista; el modelo solo agrega la frase, o cubre lo que la regla no supo. **Si no está, la skill sigue funcionando.** |
| `si` | sin modelo no hay resultado. |

Contarlas todas como «IA» infla lo que el modelo hace de verdad; contarlas como
«no IA» esconde dónde hay que mirar.

**El estado al 2026-08-19: 26 habilidades — 20 sin IA, 6 con IA opcional, 0 que
dependan del modelo.** Y hay un test que congela dos invariantes de diseño:
**ninguna skill de DETECCIÓN usa el modelo** (lo que el agente encuentra lo
encuentra una función que corre sola; el modelo aparece después, para leer
patrones entre hallazgos) y **la mayoría no lo usa** — si eso se da vuelta,
alguien está mandando al modelo trabajo que hace una función.

### 0.p EL AGENTE ES ADMIN-ONLY, PERO LO QUE MANDA LE LLEGA A CUALQUIERA (2026-08-19)

> *«El AV AGENT es SOLO para admin, no para el resto. Aunque esto no quiere decir
> que no tenga el poder para mandar una alerta, notificación, etc. a otro user
> que no sea admin.»* (user)

**Y ahí había un bug real, introducido el mismo día.** La acción
`avisar.responsable` (§0.j) deja el aviso en `mercado.av_agent_avisos` con el
email del destinatario — pero el endpoint para leerlo vivía bajo `/api/ia`, que
está gateado por el módulo **`ia`, que solo tienen admin e invitado**. O sea que
el agente le podía escribir a un trader y **el trader no lo veía nunca**: el
aviso quedaba guardado para nadie. El front lo remataba montando el modal solo
para admin.

La separación correcta es exactamente la que pidió el user:

    EL AGENTE          admin-only (todo /api/ia/av-agent/*)
    LO QUE MANDA       cualquiera (/api/avisos, sin gate de módulo)

`api/routers/avisos.py` está **fuera de `/api/ia` a propósito**, y no es un
agujero:

- devuelve **solo** los avisos cuyo destinatario es el email del que pregunta.
  **No hay parámetro para pedir los de otro** — un test inspecciona las firmas de
  las rutas, no el texto del archivo;
- cerrar un aviso lleva el dueño en el **WHERE del UPDATE**
  (`AND lower(para) = %s`), no en un `if` previo: así «es mío» no es un permiso
  que alguien pueda olvidarse de chequear en el próximo endpoint que toque esa
  tabla — es parte de la escritura. Un id ajeno responde «no existe», que además
  no confirma que ese aviso exista;
- un aviso dirigido dice **qué hacer y dónde**, no expone el estado interno del
  sistema (eso sigue siendo del agente);
- el **portal invitado queda excluido igual** (REGLA #8): que hoy devolvería una
  lista vacía es una coincidencia de los datos, no una regla.

En el front es **PARA VOS**, un botón chico en la barra que **solo se dibuja si
hay algo**. Un indicador permanente en cero enseña a no mirarlo, y el día que
diga 1 tampoco se va a mirar.

**De yapa, esto apretó `/api/ia`**: al mudarse `mis-avisos`, el ÚNICO endpoint
sin `require_admin` bajo ese prefijo es el BRIEFING. El test de REGLA #8 lo
congela así.

### 0.q EL AGENTE ENTIENDE LA BASE Y LA LATENCIA (2026-08-19)

#### LA BASE DE DATOS — la información es el DELTA, no el tamaño

Pedido del user: *«que entienda qué tablas hay, cuánto pesa cada una (al menos
una vez por día) y sepa distinguir al día siguiente si se agregó algo nuevo, y
cuáles aumentaron su tamaño y por cuánto… esto tendrá que persistir para tener
contexto, pero a su vez no crecer todo el tiempo: con que tenga registro de hoy y
ayer constantemente alcanza»*.

`pg_total_relation_size` ya dice cuánto pesa cada tabla, y mirarlo una vez no
sirve de nada: son doscientos números sin escala. **Lo que informa es el
cambio**, y son tres cosas:

- una tabla **NUEVA** → alguien creó algo, o un job está escribiendo donde no
  debería. Es lo que uno se entera último y lo que explica el resto;
- una tabla que **CRECIÓ de golpe** → un job en loop, un backfill que se fue de
  mano, una tabla sin purga. Eso es lo que se come el plan;
- una tabla que **DESAPARECIÓ** → alguien dropeó algo.

Por eso hay **foto diaria** (`jobs/db_tamano.py`, 23:30 UTC) y no una consulta en
vivo: sin el de ayer no hay comparación.

**Dos fechas y nada más**, con la purga **en el mismo INSERT** (mismo patrón que
`tesoreria_snapshots`). No depende de que alguien se acuerde de correr una
limpieza: una tabla que vigila el tamaño de la base y crece sin techo es un
chiste que se cuenta solo. Son ~200 filas × 2 días.

**El corte se AUTO-CALIBRA.** ⚠️ No tengo acceso a prod, así que cualquier umbral
en MB que ponga a mano es una adivinanza (REGLA #2). Entonces no se pone: un
crecimiento entra si supera **los dos** filtros —**+25%** sobre su propio tamaño
**y** el máximo entre **10 MB y el 0,5% de la base**—. Así el aviso significa lo
mismo con 500 MB que con 50 GB, y nadie recalibra nada cuando la base crezca. Un
filtro solo no alcanza: el relativo dispara con tablas de 8 KB (crecer 50% son
4 KB) y el absoluto solo, con tablas grandes que crecen lo normal todos los días.

**La primera foto NO reporta 200 tablas nuevas.** Sin foto previa todo parecería
nuevo, y arrancar con 200 falsos positivos es la forma más rápida de que nadie
vuelva a mirar esto. Dice «es la primera, mañana te cuento» y listo.

Tablas: `manager.db_tamano` (por tabla) y `manager.db_tamano_dia` (el total de la
base, que **no** es la suma de las tablas — incluye catálogo, TOAST y espacio
libre; se guardan aparte para que las dos cifras no se puedan confundir).

#### LA LATENCIA — por qué la pantalla no servía, y qué la reemplaza

*«La verdad tengo eso en observabilidad, jamás lo usé, me está usando espacio
innecesario… ni siquiera se actualiza, puede haber cosas nuevas y no se entera.
Me interesa que el agent pueda detectar en tiempo real endpoints que estén
lentos, pero tiene que ser algo fiable y verdadero, no tirar por tirar»* (user).

**El diagnóstico es exacto y es el error de diseño que se corrige: un ranking
muestra lo LENTO, no lo ANORMAL.** Arriba de esa tabla estaban:

    /api/manager/salud/diagnostico   11.724 ms   →  es una llamada al LLM
    /api/back-office/tesoreria/dia      726 ms   →  son 1,6 s de Aunesa medidos

Los dos **están bien**. Son lentos porque hacen algo caro, y van a seguir siendo
los más lentos mañana y pasado. Una lista de cosas inherentemente lentas **no
cambia nunca** — por eso se deja de mirar, y por eso «no se entera» de nada: no
tiene con qué comparar.

**Lo que sí es información es la DEGRADACIÓN**: un endpoint que hoy tarda mucho
más que él mismo ayer. Eso no aparece en un ranking (puede seguir estando décimo)
y es lo único que amerita interrumpir a alguien. Cada endpoint se compara
**contra sí mismo**, jamás contra otros.

**Las cuatro guardas contra «tirar por tirar»** — la parte difícil, porque un
detector de latencia que grita seguido no genera desconfianza: genera que se lo
ignore, que es peor:

1. **Volumen mínimo** (20 requests). La media de 3 es ruido, no una medición.
2. **Historia mínima** (6 horas). Comparar contra dos puntos es adivinar.
3. **Relativo Y absoluto, los dos** (2,5× **y** +300 ms). Triplicarse de 10 ms a
   30 ms no le importa a nadie; empeorar 400 ms sobre 5.000 tampoco.
4. **MEDIANA y no promedio** en la línea base: un pico previo subiría la vara y
   taparía justo el problema que se repite. Hay un test que lo demuestra.

Los **5xx no pasan por la comparación**: un endpoint que rompe está roto tarde lo
que tarde, así que van con su propio umbral y sin línea base.

Corre con los detectores live **cada 5 minutos en rueda** (lo que el user llamó
«tiempo real»), y cuesta **una sola query** sobre un agregado que ya existía. La
ventana es de 2 horas y no de minutos porque el agregado es **por hora**: pedirle
a ese dato una resolución que no tiene sería inventar precisión.

⚠️ **Los cuatro números son hipótesis sin medir** (REGLA #2). Están como
constantes con nombre para moverlas con un dato real: el primer día que esto
cante algo obvio o se quede mudo, se ajustan.

#### Qué se eliminó y qué quedó

- **La tab LATENCIA de Manager se eliminó**: el ranking es justamente lo que
  falló, y dejarlo mantiene lo que enseñó a no mirar. `manager.latencia_endpoints`
  y el middleware **no se tocaron** — se eliminó la pantalla, no el dato.
- **La tab BASE queda**: el user no se quejó de esa y es el inventario. Lo que se
  suma es que el agente ahora la entienda y avise solo.
- Las dos capacidades entraron a **SKILLS solas, por la ley de §0.o** — no hubo
  que anotarlas en ningún lado. Y las dos son **`función`, sin IA**: lo que el
  agente encuentra lo encuentra una función determinista.
- Además hay **dos explicadores nuevos** (§0.m): *«¿Cuánto pesa la base y qué
  creció desde ayer?»* y *«¿Hay algún endpoint más lento que lo normal?»* — el
  segundo separa a propósito **lo degradado** (que es un problema) de **lo que
  más tiempo consume** (que es el ranking y **no** es una lista de problemas).

### 0.r EL CONTEXTO: el agente conoce la base sin que nadie se lo escriba (2026-08-19)

Pedido del user, y son dos cosas que van juntas:

> *«Que el agente sepa exactamente cada tabla que hay, y exactamente cómo funciona
> esa tabla en cuanto a los datos. Es decir: si portfolio en AuM actualiza con
> fecha T-1, ok, que el agent diga qué día es hoy, cuándo es T-1, y vaya a buscar:
> ¿hay datos? sí, no. Bueno, pasa algo o no pasa nada… Este agente me tiene que
> ayudar (y a futuro hacer solo) a controlar el sistema.»*
>
> *«Que no dependa de un git pull, que no dependa de cosas estáticas. Que siempre
> sepa qué hay en las bases, de schema y eso, o de tablas posta. Quiero modelarlo
> así al agente, sus skills con sus features, todo modularizado, que tenga un
> contexto. Hay que armarlo pro.»*

#### POR QUÉ NO HAY NINGUNA LISTA — y es la decisión que hace que esto escale

La respuesta obvia sería escribir el contrato de cada tabla: *«`portafolio.tenencia`
es diaria, `market_snapshot` es live, …»*. **Es la respuesta equivocada, por dos
razones distintas:**

1. **Nadie mantiene 200 contratos.** La lista quedaría vieja el primer mes — y una
   lista vieja es PEOR que ninguna, porque afirma cosas falsas con la misma cara
   que las verdaderas. Este proyecto ya lo pagó: por eso `MAPA_APP.md` §0,
   `SISTEMA.md` y el catálogo de SKILLS se autogeneran.
2. **Depender de un `git pull` para que el agente sepa qué existe es lo contrario
   de un agente.** Una tabla creada el martes tiene que estar en su cabeza el
   martes, no cuando alguien se acuerde de anotarla.

Así que **todo se DERIVA** (`api/services/av_agent_contexto.py`):

| qué | de dónde | mantenimiento |
|---|---|---|
| qué tablas hay | `pg_catalog` | **cero** — una tabla nueva aparece sola |
| cuál es su columna de fecha | `information_schema` + las convenciones del repo | cero |
| **cada cuánto se escribe** | **se MIDE** mirando la distribución de esa columna | cero |

Lo tercero es la parte no obvia y es la que hace que esto cubra 200 tablas: **la
cadencia no hay que declararla, la tabla la dice**. Si el intervalo típico entre
escrituras es de segundos, es `tiempo_real`; si es de un día hábil, es
`diaria_habil`. Siete clases: `tiempo_real` · `intradiaria` · `diaria` ·
`diaria_habil` · `semanal` · `mensual` · `eventual` / `estatica` / `vacia`.

**Se mide con la MEDIANA, no el promedio.** Un fin de semana, un feriado o un
backfill viejo desplazan la media y dejarían a una tabla diaria pareciendo
semanal — con lo cual se le dejaría de exigir frescura justo a la que importa.
Y se mide sobre las **últimas** escrituras: una tabla que hace un año era diaria y
hoy es live tiene que decir live.

#### Dónde SÍ manda un contrato declarado, y por qué

⚠️ **La cadencia aprendida tiene un punto ciego que hay que entender:** si el job
de tenencias lleva tres días roto, la «normalidad observada» de la tabla **se
corre sola** y el detector deja de avisar — se acostumbra al problema.

Por eso los 8 `CONTRATOS` declarados de `salud.py` **siguen mandando donde
existen**: ahí el «debería» es una decisión de negocio (`portafolio.tenencia` DEBE
tener el último día hábil), no un promedio. La derivación cubre las otras ~190,
que hoy eran un **punto ciego total**. Y el detector nuevo **excluye** las que ya
tienen contrato, para que el mismo problema no aparezca dos veces con dos textos.

Es la misma lógica que en la latencia (§0.q): compararse con uno mismo detecta un
CAMBIO, y no detecta algo que está mal desde siempre.

#### Tres cosas que evitan el falso positivo

- **El fin de semana no cuenta.** Sin eso, toda tabla de días hábiles aparece
  atrasada cada lunes a la mañana.
- **A una tabla sin ritmo no se le exige frescura.** `eventual`, `estatica` y
  `vacia` devuelven `no_se_puede_saber`, no `atrasada`: a una de carga manual no
  se le puede pedir que escriba, y marcarla en rojo todos los días es cómo se
  entrena a alguien para ignorar una pantalla.
- **Los topes son generosos** (3-4× el intervalo típico). Un detector que avisa al
  primer atraso avisa todos los días, y de uno así no se desconfía: se lo ignora.

`manager.tabla_perfil` es solo la MEMORIA de la medición (una fila por tabla,
upsert, no crece), para no re-medir 200 tablas cada vez que alguien pregunta —
sin eso el agente nunca preguntaría. Se refresca en el job diario, junto con la
foto de tamaño: las dos contestan «¿cómo está la base?» y separarlas daría dos
horarios y dos cosas que pueden fallar.

#### LOS MOTORES — todo estaba hecho y el agente no lo miraba

*«Con los logs de los motores lo mismo: quiero que si hay alguno caído enterarme
rápido (y a futuro que pueda hacer algo)»*.

`api/services/diagnostico_registry.py` ya tiene **53 piezas** —15 motores, 33 jobs,
5 APIs— **cada una con su cadencia, su ventana horaria, su umbral y de dónde se
lee la frescura**, y `diagnostico.arbol()` las evalúa. Pero eso vivía SOLO en la
pantalla de Manager → DIAGNÓSTICO, o sea que había que ir a mirarla.

`av_agent_motores.py` **no reimplementa nada**: lee el mismo árbol y convierte lo
que está roto en un hallazgo. Reescribirlo habría dado dos verdades sobre si un
motor anda. Corre en el monitor de rueda, cada 5 minutos.

⚠️ **La VENTANA es lo que hace que esto no mienta.** Un motor fuera de rueda **no
está caído: está apagado** — los prende y los apaga el cron de lunes a viernes.
Sin mirar la ventana, este detector cantaría quince motores muertos todos los
sábados, y en dos fines de semana nadie volvería a leerlo. Y `lento` **no** se
reporta: un motor que tarda el doble sigue produciendo, y mezclarlo con uno muerto
pierde la diferencia entre las dos cosas.

#### El árbol tenía NUEVE agujeros, y el anti-drift lo decía hace meses

Al revisar el primer deploy apareció que `tests/test_diagnostico_registry.py`
—el test anti-drift que cruza el registro contra `deploy/crontab.txt`— **estaba en
rojo**: nueve crons corrían sin estar en el árbol. Como el agente ahora LEE ese
árbol, cada agujero es una pieza que no vigila nadie.

Se clasificaron uno por uno, y la clasificación importa más que el número:

- **TRES eran piezas de dato de verdad** y entraron al árbol: `tamar_1816` (la
  TEA/margen de la pata TAMAR — si el feed se corta la celda queda vacía **y la
  otra pata sigue viva**, así que todo «parece bien»), `tesoreria_echeq_recibidos`
  (el espejo de los depósitos; si muere, el equipo los carga a mano sin enterarse)
  e `interbanking_sync` (los extractos: es la única fuente de esos saldos, y al
  cortarse la tab **no miente, se queda quieta** — que es peor de detectar).
- **TRES son el agente mirando al sistema** (`av_agent`, `av_agent_live`,
  `db_tamano`): quedan fuera a propósito. Meterlos al árbol sería pedirle al árbol
  que se vigile a sí mismo.
- **TRES son mantenimiento de catálogo** (`assets_autofill`,
  `validar_instrumentos`, `ficha_1816`): escriben metadata (ticker, emisor,
  vigencia, símbolos), no el dato que la vista muestra. Si un día no corren, la
  pantalla sigue mostrando lo mismo.

**Lo que dejó de lección:** el test existía y avisaba; lo que faltaba era que
alguien pagara el costo de clasificar. Un cron nuevo entra a `_CRONS_IGNORADOS` en
diez segundos y ahí queda invisible para siempre — por eso ahora cada entrada de
esa lista lleva **por qué** no es una pieza de dato, no solo el nombre.

#### La gracia del arranque — el falso positivo que se cazó ANTES de que pasara

⚠️ **La ventana del árbol abre 20 minutos ANTES de que los motores arranquen:**

    10:00 ART   `_APERTURA["rueda"]` — la ventana del árbol abre
    10:03-10:06 las piezas empiezan a dar CRÍTICO (umbral × 3, son 60-120 s)
    10:20 ART   los motores ARRANCAN de verdad (`20 13 * * 1-5` del crontab)

Son **~17 minutos de falsos positivos todos los días**. En la pantalla de
DIAGNÓSTICO eso ya pasaba y no molestaba —había que ir a mirarla—; como hallazgo
del agente sería **un aviso en ALTA cada mañana a la misma hora**, que es la
forma más rápida de que se deje de leer. Se detectó al revisar el primer deploy,
antes de que el detector llegara a su primera rueda.

`GRACIA_ARRANQUE_MIN = 30` **no es un umbral de tolerancia**: pasado ese rato, un
motor que no produce SÍ está caído y se canta. Y la gracia vive en el detector, no
en `_APERTURA`: esa ventana la comparte la pantalla de DIAGNÓSTICO, y moverla para
arreglar el detector cambiaría el estado de una vista que nadie pidió tocar.

#### La pregunta que nadie contestaba de una

*«No quiero que me muestre todos los endpoints; yo quiero saber que en horario de
mercado la aplicación funciona bien y no hay nada colapsando.»*

Explicador **«¿Está todo funcionando bien ahora?»**: junta las TRES patas —los
motores, la frescura de las tablas y la velocidad— y arranca con el veredicto en
una línea. Las tres existían, en tres pantallas distintas, y por eso había que
saber de antemano dónde mirar para poder preguntarse si el sistema andaba.

**Qué NO se muestra**: el conteo, no las 53 piezas; solo se detallan las rotas. El
user fue explícito con que no hace falta mostrar todo en el modal, **pero que por
dentro se haga**.

#### Lo que queda para «que a futuro haga algo solo»

El agente ya **ve** un motor caído y una tabla quieta. Poder **relanzarlos** es la
acción que sigue, y entra por la arquitectura de §0.j (proponer → tu OK → aplicar
→ verificar) — con la diferencia de que ahí el «verificar» es esperar a que el
motor vuelva a escribir, no releer una fila.

### 0.s ¿LOS PERMISOS SON REALES O ESTÁN EN LOS PAPELES? (2026-08-19)

> *«Que sea capaz de detectar si algún endpoint está mal hecho y se puede
> consultar a la fuerza por tener solo permisos "en los papeles". Una vez me
> había pasado que Vercel me dejaba todo sin protección y no me daba cuenta.
> Todos los endpoints debería ser capaz de controlar que solo los ves si tenés el
> permiso.»* (user)

#### PRIMERO: el agujero que había, medido

`CLAUDE.md` ya advertía que el tooling de seguridad estaba ciego por la trampa de
FastAPI. **Al medirlo resultó peor de lo que decía:**

    gen_mapa_app (que la resolvía)              541 rutas
    audit_rbac · test_rbac_superficie            37 rutas

O sea que el test llamado «RBAC de superficie» auditaba el **7%** de la superficie
**y pasaba en verde**. Para una herramienta de seguridad ese es el peor modo de
falla posible: no avisa que no sabe — **afirma que está todo bien**.

Y `gen_mapa_app`, el que se creía correcto, tenía su propio bug: **duplicaba el
prefijo en 395 de 541 paths** (`/api/ia/api/ia/observabilidad`), porque el
`prefix` de un `APIRouter` ya viene aplicado a sus propias `APIRoute` y se lo
volvía a sumar. Los gates estaban bien; los paths publicados en `MAPA_APP.md` §0,
no. Un path que no existe es **peor** que uno faltante: cualquier herramienta que
intente PROBARLO recibe un 404 y concluye que está protegido.

**El arreglo no fue copiar la técnica tres veces: fue que haya una sola.**
`api/superficie.py` es ahora la única forma de recorrer la superficie, y las tres
herramientas delegan ahí. Mientras la técnica viva en tres archivos, dos van a
quedar viejos — ya pasó.

**Lo que apareció al destapar el test:** 13 escrituras que figuraban «sin gate».
Verificadas una por una, **12 no eran agujeros**: ACA y Mesa de Dinero se gatean
con allowlists per-usuario (`require_escritura_*`), a propósito y documentado, y
el test solo sabía reconocer `require_module`. Ahora las reconoce, **enumeradas
explícitas** — no con un `startswith("require_")` que trague cualquier cosa.

**La 13ª sí era real y era nueva**: `/api/avisos` (de §0.p) no pedía bearer. Su
única protección era CF Access en el borde — justo lo que no se puede dar por
sentado. Se le agregó, y de paso **el rechazo del invitado dejó de ser un `if`
adentro del handler y pasó a ser una dependency del router**: un `if` en el
cuerpo de una función **no lo puede ver ni el test ni el propio agente**, así que
ese router figuraba como escritura sin ningún gate. *Un permiso que existe pero no
se puede auditar es, para cualquier herramienta, un permiso que no existe.*

#### SON DOS PREGUNTAS DISTINTAS, y separarlas es todo el diseño

| | qué contesta | cómo |
|---|---|---|
| **Lo DECLARADO** | ¿cada endpoint tiene el gate que le corresponde? | se **LEE** del árbol de rutas |
| **Lo EFECTIVO** | ¿el borde realmente lo aplica? | se **PRUEBA** |

La segunda **no se puede leer de ninguna manera**: el backend puede estar
impecable y el borde abierto, que es exactamente lo que le pasó al user con
Vercel. La única forma de saberlo es **pedir sin credenciales y ver qué
contesta**.

Es la primera vez que el agente hace una **prueba activa** en vez de observar. Y
es la idea de **recompensa verificable** de §0.j aplicada a seguridad: no *«creo
que está protegido»* sino *«pedí y me dio 401»*.

**El invariante**, binario y sin listas que mantener:

> **Ningún endpoint puede contestar algo distinto de 401/403 sin credencial.**

#### TRES LÍMITES, y hay que decirlos

1. **Un agente que prueba endpoints sin credenciales es, técnicamente, un
   scanner.** Por eso: **solo `GET`**, solo rutas del inventario propio (no
   adivina URLs), con throttle, y **jamás una escritura** — probar un `POST` «a
   ver si me deja» puede escribir de verdad. Congelado por test.
2. **Desde dónde se prueba cambia qué se prueba.** Desde el Droplet, pegarle a la
   URL pública sale a internet y vuelve por Cloudflare → verifica CF Access y el
   backend. **NO verifica Vercel**, que es donde el user tuvo el problema. Por eso
   el resultado **siempre** declara su alcance: en seguridad una media verdad se
   lee como un sí, y un verde sin alcance da falsa tranquilidad justo en la capa
   del incidente.
3. **Solo rutas sin parámetros de path.** Con `{id}` no se puede armar una URL
   real y un valor inventado devuelve 404, que no dice nada sobre permisos. Esas
   quedan cubiertas por la lectura.

**Y no se inventa la URL**: sin `AV_AGENT_URL_PUBLICA` el chequeo **no corre**.
Probar contra el host equivocado y salir en verde es peor que no probar.

Un **404 no es un hallazgo** (no dice nada: puede ser una ruta con parámetros o un
deploy a medias) y un **405 cuenta como rechazo** (la ruta existe, el método no
aplica: tampoco filtró nada). Reportar los mudos sería el ruido que enseña a
ignorar el aviso.

#### Dónde corre

La parte que **lee** es gratis y corre en el job nocturno junto con la foto de la
base. La que **prueba** hace tráfico real contra producción, así que va ahí
también y **no** en el monitor de rueda: 400 requests cada cinco minutos molestan,
y una superficie mal gateada no se arregla sola en ese rato.

Explicador **«¿Están bien protegidos los endpoints?»**, que muestra las dos capas
por separado — porque una se arregla en el router y la otra en el borde.

#### LO QUE ENCONTRÓ LA PRIMERA CORRIDA (2026-08-19, en prod)

Seis avisos, todos del handshake OAuth del MCP. **Verificados uno por uno contra
`docs/MCP.md` y `CLAUDE.md`, cinco no eran agujeros y el sexto sí valía la pena
—pero por otro motivo del que parecía.**

Los cinco son **públicos por protocolo**: el cliente los lee ANTES de tener
credencial, así que pedirle credencial para averiguar cómo sacar una credencial
no cierra nunca. Son los tres `.well-known/*` (discovery, RFC 8414 / RFC 9728),
`/oauth/register` (DCR, RFC 7591 — devuelve credenciales NUEVAS, no ajenas) y
`/oauth/token` (su **input** ES la credencial: code + PKCE verifier).

**El sexto es `/oauth/authorize`, y es una categoría distinta:** ahí logea el
usuario, o sea que NO es público — lo que pasa es que **su candado vive en
Cloudflare Access, no en el repo**. Declararlo como «abierto a propósito» habría
sido mentir, y peor: lo habría sacado del radar justo donde el user ya se quemó
(*el código impecable, el borde abierto, cero errores visibles*).

Por eso son **DOS listas y no una**, y la diferencia no es cosmética — cambia qué
hay que verificar:

| lista | qué significa | qué se prueba |
|---|---|---|
| `ABIERTOS_OK` | público de verdad: no devuelve nada de nadie | nada que probar |
| `PROTEGIDOS_EN_EL_BORDE` | el candado existe, pero vive afuera del repo | **siempre**: es lo único que solo la prueba puede juzgar |

La segunda lista **fuerza** su prueba activa: el filtro general descarta las rutas
sin gate (`not r.sin_gate`) y justamente estas no tienen gate en el código a
propósito. Si `/oauth/authorize` contesta **200 sin credencial** en vez de desviar
al login, la app de CF Access que lo cubre no está, se despublicó o le cambiaron
el path → hallazgo `borde_sin_candado`, severidad alta.

De paso, un **3xx al login cuenta como RECHAZO**, no como fuga: así contesta CF
Access sin sesión, y se reconoce por el host del `Location`
(`cloudflareaccess.com`), no por el código. Contarlo como fuga habría llenado el
aviso de falsos positivos, que es exactamente como se aprende a ignorarlo.

#### EL SILENCIO NO ES UN VERDE

La primera corrida también mostró un hueco **del reporte, no del chequeo**: el job
imprimió los seis avisos y nada más, así que un lector razonable concluye que el
borde se probó y salió bien. **No se probó** — falta `AV_AGENT_URL_PUBLICA`.

Un job que solo habla cuando encuentra algo se lee igual esté sano o esté ciego.
Ahora el detector emite un hallazgo propio (`prueba_no_corrio`, severidad media) y
el job **siempre** cierra diciendo qué cubrió: *«N rutas leídas + el borde probado
(no cubre Vercel)»* o *«N rutas leídas — **el borde NO se probó**, falta X»*.
Congelado por test, porque es el mismo principio que el `alcance`: **en seguridad
una media verdad se lee como un sí.**

**PENDIENTE (REGLA #6, se pide una vez):** `AV_AGENT_URL_PUBLICA` en el `.env` del
Droplet (`https://api.acaquant.com`). Sin eso, la mitad que PRUEBA queda apagada y
solo corre la que lee.

### 0.t EL AGENTE PERSISTE — o no sirve de nada (2026-08-19)

> *«Esto no tiene que ser estático, ¿entendés? Porque si no pasa esto: hago la
> solicitud, se encuentra algo o no pasa nada, y en el medio pasa el tiempo,
> avanza la app, se agregan cosas nuevas y se vuelve a quedar desactualizado
> todo. El agente debe PERSISTIR: no tengo que estar constantemente pidiéndole
> cosas, ya tiene que tener mapeado todo.»* (user)

#### Lo primero: tres detectores escribían en el vacío

Al revisar la pregunta apareció que **`permiso_flojo`, `tabla_quieta` y
`db_cambio` corrían todas las noches y solo `print`eaban en el log del job**. El
hallazgo moría ahí. Y la tab SKILLS decía, para los tres, *«se ve en AV Agent →
ENCONTRÓ»*.

*Un catálogo que promete algo que la pantalla no da es peor que no tener
catálogo*: manda a buscar a un lugar donde no está. Eran capacidades reales,
funcionando, invisibles.

**Ahora se persisten** (`jobs/db_tamano` → `mercado.av_agent_hallazgos` con
`alcance='sistema'`) y aparecen en ENCONTRÓ como cualquier otro hallazgo. Dos
detalles que no son detalles:

- **`sistema` es un alcance de REEMPLAZO, no una corrida.** Cada pasada pisa la
  anterior entera, así lo que se arregló desaparece solo sin que nadie lo marque.
- ⚠️ **Y por eso hay que excluirlo del `max(corrida_at)`**, igual que `live`: un
  máximo a secas devuelve siempre el del último monitor y **la relevada nocturna
  entera desaparece de la pantalla, en silencio**. Ese bug ya se había pagado con
  `live` y estaba a punto de volver a pasar. Ahora hay una constante
  (`av_agent.ALCANCES_VIVOS`) y un solo INSERT compartido — dos copias de la
  misma transacción se separan el día que una cambia.

#### La ley de SKILLS necesitaba una segunda mitad

La tab garantizaba que **toda habilidad aparezca**. No garantizaba que lo que la
habilidad dice de sí misma sea cierto. Así que cada detector declara ahora **en
qué job corre**, y hay un test que exige que ese job **escriba hallazgos** — no
que exista: que escriba. Un detector cableado a un job que solo imprime es un
`print` con buena prensa, y eso es exactamente lo que había.

**El horario NO se declara: se lee de `deploy/crontab.txt`**
(`jobs_catalogo.schedules_por_modulo`). Un horario copiado a mano en otro archivo
se desincroniza el día que se cambia uno de los dos, y nadie se entera hasta que
importa. En la tab cada habilidad muestra ahora **«corre solo · jobs.x · 23:30
UTC»**, que es la mitad de la respuesta a *«¿esto se mantiene al día o hay que
pedírselo?»*.

#### La memoria de la superficie: de foto a DELTA

Un chequeo sin memoria solo sabe decir **cuántos** endpoints están abiertos hoy.
Es exactamente el problema que el user describe: la app avanza y el chequeo
vuelve a quedar viejo, porque cada corrida arranca sin saber nada de la anterior.

`manager.superficie_dia` (hoy y ayer, purgadas en el mismo INSERT — el mismo
contrato que `db_tamano`) le da lo que faltaba:

| | por qué importa |
|---|---|
| **apareció un endpoint NUEVO sin gate** | alguien lo publicó así hoy: hay un culpable identificable y se arregla en el momento |
| **un endpoint PERDIÓ el gate que tenía** | **la regresión, y es invisible para cualquier foto**: se cierra uno, se abre otro y el total de abiertos no se mueve |

La segunda (`perdio_el_gate`, severidad alta, y canta aparte si además **escribe**)
es la que justifica toda la tabla. Y el `sin_gate` de siempre ahora dice **desde
cuándo**: `hoy` · `ya estaba` · `sin foto previa` — porque afirmar «ya estaba» sin
haber mirado ayer sería inventar un dato, y *«no sé» es una respuesta válida*.

La **primera** corrida no reporta nada del delta: el día uno todas las rutas son
«nuevas», y avisar de 541 endpoints nuevos es la forma más rápida de que el aviso
se apague para siempre. Mismo criterio que la primera foto de la base.

#### Dónde se ve, y dónde NO

Los cinco tipos del sistema (`permiso_flojo`, `motor_caido`, `tabla_quieta`,
`latencia`, `db_cambio`) entraban a la pantalla con el **tipo crudo de
encabezado** (`PERMISO_FLOJO`), sin chip en la fila de filtros y con el sujeto
cortado a 72px —donde no entra `/api/portfolio/aum` ni
`mercado.market_snapshot`—. Los tres arreglados: etiqueta en castellano, chip, y
la columna ancha que SALUD ya tenía por el mismo motivo. Van **arriba** de los
hallazgos de datos, por el mismo criterio de «aguas arriba» que ordena las
lentes: un motor caído o un permiso abierto explica —o vuelve secundario—
cualquier bono mal cargado de más abajo.

**No salen en AVISOS y es a propósito.** Los avisos (§0.p) son mensajes
DIRIGIDOS a una persona; esto es estado interno del sistema y el agente es
admin-only. Tampoco interrumpen todavía: la interrupción vive en las transiciones
de SALUD. Que un `perdio_el_gate` sobre un endpoint de ESCRITURA abra el modal
solo es el paso siguiente.

### 0.u EL RELOJ DEL MERCADO — la calibración que hizo honesto al monitor (2026-08-19)

> *«El agente tiene que entender el horario de mercado, los motores, los horarios.
> No puede decir solo desde cuándo no actualiza algo, porque eso es mentiroso. En
> AVISOS tenía un montón de avisos de precios sin precio, pero eso era porque el
> MERCADO ESTABA CERRADO: es obvio que no va a actualizar si el precio cierra a
> las 17 y abre a las 10:30. Ahora es relevante entender por qué uno de los bonos
> que aparece en la tabla no tiene precio DURANTE LA RUEDA — si es por liquidez o
> porque no suscribe porque hay algo mal, como es el caso del AO29, que claramente
> hay algo mal y ni lo está detectando.»* (user)

La primera corrida del monitor del sistema cantó **58 hallazgos**. El diag los
agrupó y el resultado fue el que hacía falta para no creerle: **47 de 57 tablas
«quietas» eran de la misma cadencia**, con atrasos de 47 minutos a 43 días. Eso
no son 47 problemas — **son dos bugs del detector**, y ninguno se arregla subiendo
una tolerancia.

#### Bug 1 — una RÁFAGA no es un ritmo

La cadencia se medía como la mediana del intervalo entre escrituras consecutivas.
Una tabla de **auditoría o un catálogo** se escribe a los saltos: alguien edita y
entran 15 filas con dos segundos de diferencia, y después nada por tres semanas.
La mediana mira **adentro** de la ráfaga y dice *«tiempo real»*.

Por eso `clientes.aca_valores` —sin escribir hace **43 días**— aparecía
clasificada como live y por lo tanto atrasada. Lo mismo `senebis_agentes`,
`contrapartes`, `role_audit`, `ons_ignoradas`: **todas tablas de evento**.

El arreglo no es tocar el umbral: es **preguntar otra cosa**. Una tabla con ritmo
rápido escribe **casi todos los días**; una a ráfagas, no. Se mide igual que todo
acá —observando, sin declarar nada— contando en **cuántos días distintos** escribió
en el último mes; la que no llega se degrada a `eventual`, que ya significa *«no se
le puede exigir frescura»*. Y si la medición falla, **no se degrada nada**: «no pude
mirar» jamás puede convertirse en un veredicto.

#### Bug 2 — el atraso se medía en tiempo de RELOJ

`mercado.timesales` a las 20:30 ART lleva 3½ horas sin escribir. Eso **no es un
atraso**: el mercado cerró a las 17. Con tiempo de reloj, el job de las 23:30
marcaría todas las tablas de rueda **todas las noches, para siempre** — y un aviso
que aparece siempre a la misma hora se deja de leer en una semana.

Ahora el atraso de una cadencia intradía se mide en **segundos de mercado
abierto**. Es la misma idea de `_segundos_de_finde` (que ya existía para las
diarias hábiles) llevada a su forma general, y el mismo principio que el detector
de motores: *fuera de rueda no está caído, está apagado*.

    último dato          ahora            atraso de RELOJ    atraso de RUEDA
    ─────────────────────────────────────────────────────────────────────────
    ayer 16:58 ART       hoy 20:30 ART         27,5 h              7 h  ← real
    hoy  16:58 ART       hoy 20:30 ART          3,5 h              0    ← cerrado
    viernes al cierre    lunes 10:30 ART          65 h             30 min

**Y no es indulgencia**: una tabla de tiempo real que se pasó la rueda ENTERA sin
escribir sigue saliendo atrasada. De yapa resuelve el arranque — a las 10:10 ART
hace diez minutos que abrió, así que lo de ayer al cierre acumula diez minutos, no
diecisiete horas.

#### Lo mismo, del otro lado: los avisos que sobrevivían al cierre

El monitor de rueda corre 10:30-17 y **reemplaza** lo suyo en cada pasada. Pero al
cerrar deja de correr, y su última foto —la de las 16:55— **se quedaba en la
pantalla toda la noche y todo el fin de semana**. Eso es lo que el user veía como
«avisos de sin precio con el mercado cerrado»: *el detector estaba bien, la foto
estaba vieja*, que para el que mira es lo mismo.

Ahora un hallazgo de rueda **vence** (`av_agent.VENCEN_EN_S`, 15 min). Vence en
vez de borrarse con un cron al cierre, y eso es a propósito: **si el monitor se
muere a las 11, sus hallazgos también desaparecen** — y está bien, porque ya no
sabemos si siguen pasando. Un dato que nadie refresca no puede seguir afirmándose.
Se cura solo y no depende de que ningún job corra a la hora justa.

#### El AO29, resuelto: no era el precio, era la PATA

El diag desmintió mi hipótesis —y por eso existe—. El AO29 pasa la cadena
entera: master, símbolo, seis patas validadas, Primary lo lista, y el snapshot
lo actualiza cada pocos segundos con **métricas completas y sanas** (TEA 9,73%,
paridad 91,4, duration 2,88). Los contadores del barrido dieron **0 sin símbolo,
0 sin snapshot, 0 con precio y sin métricas** — la regla que iba a escribir no
habría cazado nada.

Lo que sí estaba mal se ve al comparar el bono con su familia:

    AO27   102,00      paridad 102,00     cotiza en DÓLARES
    AO28    94,80      paridad  94,80     cotiza en DÓLARES
    AN29    92,65      paridad  92,65     cotiza en DÓLARES
    AO29   139.300     paridad  91,41     cotiza en PESOS   ← el raro

**Hay DOS fuentes de símbolos y nadie las cruzaba.** `curvas.instrumento` —lo que
el motor suscribe— se carga **a mano**; `mercado.especies` sabe cuál es la pata
correcta (`es_default`) y se deriva de Primary. Medido: de **229 bonos, 3** no
coinciden — **AO29, GD46 y CO32** — y son exactamente los tres que muestran pesos
en una curva en dólares. El GD46 es el que el user ya había cazado a ojo el
2026-08-18; los otros dos son el mismo bug sin descubrir.

Esto **cambia el veredicto anterior**. El 2026-08-18 la regla `cotiza_en_pesos`
se había bajado a `baja` con el texto *«la valuación está bien, es contexto»* —
correcto sobre la valuación y equivocado sobre la causa: no es que el bono cotice
así y no haya nada que hacer, es que **la pata correcta ya existe, está validada,
y el arreglo es un campo**. Ahora, cuando `especies` marca otra default, el
hallazgo es `pata_equivocada` con severidad **media** y nombra el símbolo exacto.
No `alta`: la valuación sigue estando bien, no hay plata mal contada.

⚠️ **Y el hallazgo avisa que hace falta reiniciar el motor.** El universo se arma
al arrancar, así que cambiar el campo **no surte efecto hasta el próximo
reinicio** — y reiniciar en rueda corta el feed de la mesa. Por eso mismo esto
**no** se convirtió en una ACCIÓN automática todavía: el ciclo de §0.j verifica
releyendo la fila, diría «aplicada» y la pantalla seguiría igual hasta la noche.
*Una acción que se aplica y no se ve destruye la confianza en todas las demás.*

#### Y el final: el motor y la pantalla no se estaban hablando

Nada de lo anterior explicaba **por qué la fila salía en `--`** teniendo el precio
y las métricas cargadas. El agente decía «está todo bien» y tenía razón sobre lo
que él miraba; la pantalla mostraba vacío y también tenía razón. **Las dos cosas
pueden ser ciertas si cada uno está mirando un símbolo distinto** — y era eso:

    el MOTOR escribe el precio leyendo el BLOB   `engines/curvas.py`
    la VISTA lo busca por la COLUMNA             `… ON s.ticker = c.instrumento`

Viene del renombre del 2026-08-15, que migró las columnas y dejó el blob intacto
a propósito (lo leen ~500 lugares) **con el significado invertido**:

    COLUMNA   ticker = «AL30»              instrumento  = «MERV - XMEV - AL30 - 24hs»
    BLOB      ticker = «MERV - XMEV - …»   ticker_corto = «AL30»

Mientras los dos digan lo mismo no pasa nada, y por eso durante cuatro días no
pasó. **El problema es que nadie los estaba manteniendo iguales.** Existía el
mecanismo —`_COLS_FUERA_DEL_BLOB`, donde *la columna gana*— y cubría el emisor y
los tres ejes, pero **no los dos campos de símbolo**: eran los únicos sin árbitro.

Medido: **2 de 229** — AO29 y CO32. En los dos la columna ya tenía la pata
correcta (la D, en dólares) y el blob la vieja en pesos. O sea que el dato estaba
bien; lo que estaba mal era **quién lo leía**.

Se arregló con la misma regla, sumando los dos campos al merge **con ALIAS**
(sin él, `doc["ticker"]` pasaría a valer `AO29` y los ~500 lugares que lo usan
como símbolo de mercado se romperían todos juntos — peor que el bug original).

⚠️ **Y el efecto no es inmediato**: el motor arma su universo al arrancar, así que
hasta reiniciarlo **fuera de rueda** esos dos bonos quedan sin suscribir. Ahora el
agente los canta como `no_suscripto` — que es exactamente la señal que faltaba, y
la prueba de que la mitad que él mira y la que mira la pantalla por fin coinciden.

**La lección, que vale más que el bug:** dos representaciones del mismo dato sin
un árbitro declarado no conviven — se separan. Y cuando se separan no falla nada:
cada mitad sigue siendo internamente coherente y el sistema miente en silencio.

#### Y un error del diag, que es el mismo pecado que este módulo persigue

La primera versión corría **un solo detector** (`sin_precio`) y después imprimía
*«el agente NO reporta nada de este bono»*. Falso: `precio_moneda` **sí** lo
estaba cantando. Una herramienta que exagera su propio alcance es exactamente lo
que se encontró en la superficie HTTP (el test que auditaba el 7% y decía que
estaba todo bien). Ahora corre los dos y dice «los detectores de rueda».

#### El AO29: el bono peor cargado era el único invisible

El detector de precios abría su loop así:

    if not simbolo or not tk:
        continue

O sea que **un bono del master sin símbolo de mercado se salteaba en silencio**.
No es «no aplica»: es el peor caso posible —no puede tener precio nunca, y el
motor no falla porque ni siquiera lo intenta— y encima es **el más accionable de
todos**, porque el símbolo sale de `mercado.especies`. El detector decía distinguir
las causas del «sin precio» y justo la primera la tiraba a la basura.

Ahora es un hallazgo propio (`sin_simbolo`, alta). Y `scripts/diag_bono_sin_precio`
recorre la cadena entera de un ticker —master → símbolo → especies → universo de
Primary → snapshot → precio— y dice **en qué eslabón se corta**, que es la
diferencia entre *«no opera por liquidez»* y *«hay algo mal cargado»*.

### 0.v LA PRIMERA ACCIÓN QUE SE VE EN EL ACTO (2026-08-19)

> *«Esto que acabamos de hacer es un tipo de actitud que debe tener el agente:
> estar monitoreando y saber la solución.»* (user)

#### El error que la origina, y hay que dejarlo escrito

Buscando por qué la fila del AO29 salía vacía, se midió *«¿esta pata opera?»*
contra `mercado.timesales` y dio **0 trades en 30 días**. Se concluyó que la pata
en dólares no cotizaba. **Era falso, y de la peor forma posible.**

`mercado.timesales` la escribe el motor **solo para los símbolos que suscribe**.
Como a `AO29D` no la suscribía nadie, tenía 0 filas **por construcción**.
Preguntarle a esa tabla si un símbolo opera es preguntarle al que no estaba
escuchando si sonó el teléfono. Dos pistas lo delataban y no se miraron: **todas**
las patas no suscritas daban 0 (la firma de «solo tenemos lo que pedimos»), y la
tabla **se purga a 7 días**, así que la ventana de 30 no podía existir.

`snapshots_cierre_hist` y `market_snapshot` tienen el mismo origen. **Las tres
fuentes eran la misma fuente.**

Se lo pidió, y `AO29D` cotizaba a **USD 90,76** — que coincide al centavo con lo
que el motor venía calculando dividiendo el precio en pesos por el MEP (138.220 /
1.521,89 = 90,82). El cálculo siempre estuvo bien; lo que faltaba era escuchar.

> **La regla que queda:** el agente **no puede concluir «no existe» desde una
> tabla que solo contiene lo que él mismo pidió.** La ausencia de dato prueba que
> no estamos mirando, no que no haya nada.

#### Y de paso: se reinició el motor equivocado

`motor_curvas` **no suscribe nada** — su propio log lo dice: *«Escuchando N
tickers vía MarketSnapshot»*. Es un consumidor que lee `market_snapshot` y calcula
TEA, paridad y duration. Se lo reinició en plena rueda, cortando el feed, y no
podía cambiar ninguna suscripción. El que pide los precios es **`motor_rofex`**
(`engines/valores.py`), que arma su universo AL ARRANCAR.

#### La acción: `mercado.pedir_pata`

**No hace falta reiniciar nada.** `motor_rofex` tiene un `adhoc_watcher` que cada
5 segundos lee `mercado.adhoc_subscriptions` y suscribe lo que falte — y las
suscripciones de pyRofex son **aditivas**: no rompen las existentes. Se puede
pedir un símbolo **en plena rueda, sin cortarle el feed a la mesa**.

Eso la vuelve **la primera acción del agente cuyo efecto se puede ver en el
acto**, y por eso es la que se automatiza. La comparación con su hermana es el
criterio que hay que reusar:

| | efecto | ¿se automatiza? |
|---|---|---|
| cambiar el símbolo del master | no se ve hasta el próximo arranque del motor | **no** |
| pedir la pata (adhoc) | el motor la levanta en 5 s, en rueda | **sí** |

*Una acción que se aplica, se verifica en verde y no cambia nada en la pantalla
destruye la confianza en todas las demás.*

**Qué significa «verificada» acá**, que no es obvio: que el precio LLEGUE no
siempre se puede saber en el acto —la pata puede no operar hasta las 15—, así que
se verifica lo que la acción **sí controla** (que quedó pedida) y el detalle dice
si el precio ya entró o todavía no. Y eso no es una excusa: si nunca llega, el
hallazgo sigue a la vista. Lo que cambió es que **ahora la ausencia significa
algo**, porque estamos escuchando.

La alimenta el control `patas_sin_precio`, cuyo docstring dice explícitamente que
**no concluye nada sobre liquidez**: señala que hay un símbolo del master que
nadie está pidiendo, nada más.

### 0.w SKILLS con JERARQUÍA, y el nombre dejó de ser la descripción (2026-08-19)

> *«Necesito que en SKILLS haya jerarquías de habilidades: MERCADO,
> ADMINISTRATIVO, SEGURIDAD… Además queda feo esto así, nuevamente pasa que se
> repiten las cosas. Hay que darle forma.»* (user)

**Las dos cosas eran el mismo problema: la tab mostraba datos, no información.**

#### El nombre y la descripción eran la MISMA frase

El catálogo usaba un solo string para las dos cosas
(`nombre=_QUE_DETECTA[tipo].capitalize()`, `que_hace=_QUE_DETECTA[tipo]`), así
que cada fila imprimía la misma oración dos veces, una en negrita y otra abajo.
Y no era solo estética: **un nombre de veinte palabras no se puede escanear**, y
escanear es lo único que uno hace con una lista de 37.

Ahora son dos textos con trabajos distintos — el nombre se lee de un vistazo
(«Bonos sin precio, en rueda») y la descripción lleva el criterio adentro («…
distingue las 4 causas y mide en tiempo de mercado»). Congelado por dos tests:
uno prohíbe que vuelvan a ser iguales, el otro que el nombre pase de 60 caracteres.

#### El DOMINIO es el nivel 1, el tipo pasa a ser una etiqueta

El tipo (detecta / explica / resuelve) dice **cómo** trabaja el agente. El
dominio dice **sobre qué** — y esa es la pregunta que uno se hace primero.
Agrupado por tipo, para saber qué sabe el agente sobre seguridad había que leer
las 37 filas; un chequeo de permisos convivía con un bono sin cronograma.

    MERCADO 18 · SISTEMA 8 · DATOS 5 · ADMINISTRACIÓN 4 · SEGURIDAD 2

⚠️ **El dominio se DECLARA, no se infiere del título.** La primera versión lo
adivinaba por palabras y mandó *«¿hay algún endpoint más lento que lo normal?»* a
SEGURIDAD, cuando habla de rendimiento. Adivinar leyendo un texto es la misma
heurística frágil que este proyecto ya paga en otros lados. Hay un test que exige
que **cada detector** tenga dominio declarado: uno nuevo sin él caería en el
cajón genérico y nadie lo notaría.

### 0.x LA LISTA DE TRABAJO DEJA DE TENER TRABAJO QUE NO ES (2026-08-19)

> *«En AHORA marqué como leído un montón y siguen apareciendo grisados. Además es
> raro: dice REVISANDO cada 30 seg y arriba dice revisado hace 8 min.»*
> *«Los que ya el sistema detecta que no tienen punta son porque no tienen
> liquidez. No es un problema. Está bien que los marque como ilíquidos pero por
> defecto mostremos otra cosa.»*
> *«Los de cotiza en pesos… no ofrece una solución o algo, nada. Le falta ahí una
> feature que sepa ir a buscar y agregar.»* (user)

**Las tres son la misma queja**: la pantalla mostraba datos ciertos y ninguno de
los tres se podía trabajar. Y la tercera además escondía un error de método.

#### El botón que no cambiaba nada

Marcar visto dejaba el hallazgo **en la misma lista, en gris**. El diseño lo
había decidido a propósito y el razonamiento era bueno —*si marcar visto
escondiera el hallazgo, nadie lo marcaría por miedo a perderlo de vista*— pero de
ahí se sacó una conclusión de más. Con veinte abiertos, marcar los veinte no
cambia **nada** en pantalla, y un botón que no cambia nada se lee como roto.

La tab se llama AHORA y su contrato es *«lo que espera una decisión tuya»*. Un
hallazgo ya visto **sigue abierto pero ya no espera nada**. Así que no se
esconde: se **pliega**, contado y a un clic. Esconder y plegar se parecen y no
son lo mismo — la diferencia es si el número sigue a la vista.

#### Dos relojes con el mismo verbo

    CENSO       contra 1816 · ~29 créditos · de noche o a mano  → llena ENCONTRÓ
    VIGILANCIA  local · cada 30s en rueda · cero créditos       → llena AHORA

Los dos decían «revisado». No era un bug: la pantalla se contradecía sola porque
usaba una sola palabra para dos ritmos distintos. Ahora cada uno tiene su verbo y
**se muestran juntos** en el header — verlos al lado es lo que hace innecesario
explicarlos.

#### La iliquidez no es trabajo, pero tampoco se borra

29 `sin_punta` encabezaban ENCONTRÓ. La diferencia con su hermano es toda la
cuestión:

    no_suscripto   nadie pidió el precio  → la ausencia NO prueba nada  → NUESTRO
    sin_punta      lo pedimos y no vino   → la ausencia SÍ significa    → MERCADO

Es la **otra cara de la regla del AO29** (§0.v). Aquélla decía que no se puede
concluir «no existe» desde una tabla que solo tiene lo que pedimos; ésta dice que
cuando **sí** lo pedimos, la ausencia por fin significa algo — y lo que significa
es iliquidez, que no se arregla de este lado.

Nuevo eje `av_agent.DE_QUIEN`, **declarado y no inferido** (misma lección que el
dominio de las skills). Una regla que nadie clasificó cae en `nuestro`, que es el
lado que **no** esconde: el default nunca puede ser el que hace desaparecer cosas
sin que nadie lo decida. Y el corte no es silencioso — el contador «N del
mercado» está siempre a la vista, y buscar un ticker los encuentra igual.

#### Y el que sí era un error: «no encontré una pata en dólares»

Los 43 `cotiza_en_pesos` cerraban con esa frase después de mirar **una sola
tabla**, `mercado.especies`. No es tan circular como `timesales` —sale de
Primary— pero **se siembra a mano** con `scripts.sembrar_especies` y
`jobs.validar_instrumentos` le borra filas. O sea que puede estar incompleta, y
cuando lo está el hallazgo afirmaba de más. Es la misma forma del error del AO29,
cuatro días después y en otro módulo.

Ahora se pregunta en orden, y recién con las tres contestadas se puede afirmar:

    1. ¿la tenemos sembrada?   `mercado.especies`
    2. ¿existe en el mercado?  `manager.pyrofex_instruments` (el catálogo de
                               Primary: la única fuente que no depende de
                               ninguna decisión nuestra)
    3. ¿la estamos pidiendo?   `adhoc_subscriptions` + `market_snapshot`

Cuatro desenlaces, y se nombran distinto porque se atienden distinto: `sembrada`
(falta pedirla) · `solo_en_primary` (falta sembrarla y pedirla) · `sin_pata` (es
el instrumento, no un dato mal cargado) · `no_pude_mirar` — que **jamás** puede
leerse como los otros tres.

#### La puerta, y qué NO arregla

`api/services/av_agent_pata.py` + `/api/ia/av-agent/pata` (leer) y `/pata/pedir`
(sembrar y pedir). Es la única puerta de rueda que además **escribe**, y se
automatiza por el criterio de §0.v: el `adhoc_watcher` la levanta en 5s, sin
reiniciar y en plena rueda.

**Pedir la pata no cambia lo que muestra la grilla** — y eso hay que decirlo
antes de que alguien lo descubra solo. La grilla dibuja
`mercado.curvas.instrumento`, y cambiar ese campo es la acción hermana que sigue
**sin automatizarse** porque el motor arma su universo al arrancar. Lo que esta
puerta consigue es el paso ANTERIOR, que es el que faltaba:

    hoy      no sabemos si esa pata cotiza — nadie la escucha
    después  la escuchamos, y en 5s sabemos si tiene precio y cuál es

Sin ese dato el reinicio sería a ciegas, y **apuntar el master a una pata que
tampoco opera es cambiar un problema por otro**. Registrada en SKILLS vía control
(`patas_dolar_sin_pedir`) + acción (`mercado.pata_dolar`), derivada y no a mano,
como manda la LEY de §0.o.

#### De yapa: dos relojes adentro de SALUD, y un guard protegiendo el aire

`salud._chequeo_job` **recibía `ahora` y después leía otro reloj** (el del
sistema) para calcular la corrida esperada. En prod los dos coinciden, así que el
bug estaba dormido; lo que sí rompía era el test fundacional del módulo —el job
en `ok` sin correr hace dos días—, que congela el reloj. Es literalmente la forma
del bug del blob y la columna: dos representaciones del mismo dato sin árbitro,
que mientras coinciden no fallan. Acá el árbitro es el parámetro.

Y `test_salud_admin_only` recorría los `/api/manager/salud*` **borrados en
§0.l**: 31 casos devolviendo 404 donde esperaban 403. Un guard de seguridad
apuntando a rutas que no existen no protege nada —desde afuera un 404 y un 403 se
ven igual de cerrados— y encima bloqueaba el CI. Repuntado a
`/api/ia/av-agent/salud*`, y ahora **un 404 es FALLA y no un aprobado**: la
existencia se verifica contra la tabla de rutas (`api.superficie`) y no pegando
por HTTP, porque pegar ejecuta el handler y sin base diría «no existe» — el mismo
falso veredicto que este módulo persigue.

> La suite pasó de **42 fallas a 10**. Las 10 que quedan son
> `tests/unit/test_titulos_negativos.py` (`KeyError: 'latido'`), de la otra
> sesión (`control-saldos`).

#### LO QUE DIJO LA MEDICIÓN, incluida la parte que me deja mal parado

Corrido `scripts.diag_pata_dolar` contra prod, 44 bonos de curva USD cotizando en
pesos:

    sembrada           28   63,6%
    solo_en_primary     0    0,0%   ← el caso que este fix venía a destapar
    sin_pata           16   36,4%

**`solo_en_primary` dio CERO.** O sea que el fix de las dos fuentes **no destapó
un solo caso**: hoy `mercado.especies` no le falta ninguna pata que Primary sí
liste. La hipótesis de que la tabla estaba incompleta era razonable y resultó
falsa, y queda escrito porque medir para confirmar lo que uno ya creía no es
medir.

Lo que el cambio sí compra, y no es poco: **las 16 `sin_pata` pasaron de ser una
afirmación sin respaldo a una verificada contra las dos fuentes**, y el día que
el catálogo se mueva —que se mueve— el detector no vuelve a mentir solo. Un
detector correcto por casualidad deja de serlo sin avisar.

#### Y lo que la medición SÍ encontró, que era otra cosa

**Las 28 con pata sembrada tenían las tres columnas iguales: sin pedir y sin
precio.** El AO29 replicado 28 veces — la pata existe, está validada, y nadie la
escucha, así que su falta de precio no prueba nada.

Pero al escribirlo apareció que **el propio diag no podía sostener esa frase**:
filtraba el snapshot por `last_price > 0`, con lo cual «nadie la suscribe» y «la
suscribimos y el mercado no dio punta» salían idénticos. Es el mismo pecado, un
nivel más abajo. Ahora son **TRES** estados y no dos, en el diag y en la puerta:

    no_escucha   no está en `market_snapshot` → nadie la pide → no prueba NADA
    sin_punta    está y sin precio → la escuchamos y no vino → ESO es iliquidez
    con_precio   cotiza, y sabemos a cuánto

Y `pedible` ahora mira **estar en el snapshot**, no el adhoc: el motor puede
suscribir una pata desde el master o desde el universo de portfolio sin ningún
adhoc, así que lo que prueba que la escuchamos es que la fila exista, no cómo se
pidió. Ofrecer «pedirla» sobre algo que ya se está escuchando sería un botón que
no cambia nada — justo lo que rompe la confianza en todos los demás.

#### 8 de los 44 no eran casos: los DÓLAR LINKED

D15E7, D30O6, D30S6, D31G6, D31M7, TZV27, TZV28 y TZVD8 salían en la lista y **no
tienen nada de malo**: un dólar linked se denomina en USD y **paga en pesos**, así
que cotizar en pesos es su definición, no un síntoma. No tiene pata en dólares ni
la va a tener.

Es exactamente el error que este mismo detector ya había cometido cuando marcaba
`alta` a 46 bonos sanos: *un detector que canta casos correctos enseña a ignorar
la lista*. Se excluyen — no se les baja la severidad, porque no hay nada que
mirar. Se reconocen por `ajuste`/`ajuste_alt` y, de respaldo, por `curva`: los
ejes son nullable a propósito y exigir solo el eje dejaría pasar a los que
todavía no se clasificaron.

**Queda la lista real en 36 casos, de los cuales 28 son accionables en el acto.**

Corrido de nuevo con el filtro puesto: **31 dólar linked excluidos** y la lista
en **20**. Son muchos más que los 8 que se veían en la primera tabla, y el motivo
importa: 8 tenían `curva = 'dolar_linked'` y los otros ~23 estaban escondidos
bajo `curva = 'soberanos'` / `'on_energia'` con el EJE en dólar linked — ONs
corporativas dólar linked, que son un montón. Mirar solo el nombre de la curva
habría dejado el 74% del ruido adentro.

Y de los 12 que tienen pata: **`no_escucha` 12, `sin_punta` 0**. Ni una sola está
siendo escuchada, así que de ninguna se puede afirmar hoy que no cotiza.

### 0.y EL NOMBRE NO ES LA IDENTIDAD — las patas por FICHA (2026-08-19)

> *«Justo los BOPREAL no es que cambia la D al final, cambian al principio. Pero
> también puede intentar buscar por maturity… o sea hay maneras de decir bueno a
> ver, el ticker en ARS cuál es el underlying acá, y después decir che ¿este
> underlying está en otro CCY? Y también reforzar con underlying más igual
> maturity.»* (user)

El user lo cazó abriendo Manager → Títulos · Instrumentos, que es el discovery
crudo de Primary:

    MERV - XMEV - BPOA7 - CI     ARS   BOPREAL S. 1 A VTO31/10/27 U$S CG
    MERV - XMEV - BPA7D - 24hs   USD   BOPREAL S. 1 A VTO31/10/27 U$S CG
    MERV - XMEV - BPA7C - CI     USD   BOPREAL S. 1 A VTO31/10/27 U$S CG

**La pata en pesos se llama `BPOA7` y la de dólares `BPA7D`: se cae la O del
medio.** Las dos convenciones que `core/especies` conocía fallan las dos —el
sufijo D/C y el par O/D de las ONs— y `RE_ESPECIE` encima *acierta a medias*, que
es lo peor: clasifica `BPA7D` como base `BPA7`, especie MEP, y lo cuelga de un
bono que no existe. El par nunca se arma y los 6 BOPREALes salen como «no tiene
pata en dólares» teniéndola.

#### Por qué NO se le agrega un caso a la regex

Sería la tercera convención escrita a mano, y la cuarta la vamos a descubrir
igual que ésta: tarde, y porque un bono se veía raro en la pantalla. El problema
de fondo es que **estábamos usando el nombre como identidad**, y el nombre es una
convención del emisor, no un dato.

**Primary ya dice de qué bono es cada símbolo.** El discovery guarda `underlying`
y `maturity` desde siempre y nadie los estaba usando:

    el ticker en pesos → su ficha (underlying, maturity)
    esa misma ficha    → ¿qué otros símbolos la tienen, en otra moneda?

Eso es un **JOIN EXACTO sobre dos campos**, no una heurística: o la ficha coincide
o no. Anda para BOPREAL, para AL30 y para la convención que se les ocurra
mañana, porque no mira el nombre. Vive en `core/especies.hermanas_por_ficha` y lo
usan la puerta del agente y el sembrador — una sola implementación.

⚠️ **SOLO los símbolos `MERV - XMEV - …`, y no es un detalle de formato.** Primary
publica los mismos papeles dos veces y la forma corta trae un `underlying`
GENÉRICO:

    MERV - XMEV - BPOA7 - CI   →  "BOPREAL S. 1 A VTO31/10/27 U$S CG"   ← sirve
    BPOA7/CI                   →  "Bopreales - Bonos BCRA"              ← NO

Con la genérica, los 6 BOPREALes comparten ficha y cada uno hereda las patas de
los otros cinco. **Un emparejamiento silencioso y equivocado es peor que
ninguno**: el motor pediría el precio de otro bono y la fila se llenaría con un
número perfectamente creíble. Por lo mismo hay un tope (`MAX_POR_FICHA`): una
ficha que agrupa más de 12 símbolos no se usa — un bono tiene a lo sumo 3
especies × 2 plazos.

#### Y el eslabón que faltaba: que el sembrador pueda escribirlo

Encontrar la pata y no poder sembrarla habría dejado un diagnóstico sin
consecuencia. `patas_de` agrupa por nombre —que es justo lo que no coincide— así
que ahora acepta `extra`: símbolos que YA sabemos de este bono por otra vía.
Entran forzados al grupo, pero **la moneda y la especie se siguen sacando del
sufijo**: `BPA7D` termina en D y el clasificador de siempre acierta. Lo único que
estaba roto era *a qué bono pertenece*, no *qué es*.

#### ¿Y ACÁ SÍ VA UN LLM? — no, y el motivo es la parte que sirve

La pregunta del user es la correcta y la respuesta es **no**. Esto es una
igualdad exacta entre dos campos: tiene UNA respuesta y la sabe el mercado. Un
modelo acá sería más lento, costaría plata, daría distinto entre corridas y —lo
peor— **sonaría igual de convencido cuando el dato no alcanza**. El proyecto ya
tiene la regla escrita al revés: las skills declaran si usan IA justamente porque
la mayoría son funciones y hay que poder verlo.

**Dónde SÍ tendría trabajo**: cuando las dos fichas no son idénticas —el mismo
bono escrito distinto en la pata de pesos y en la de dólares—. Ahí una igualdad
exacta no empareja y un modelo podría decir «son el mismo». Pero eso **hay que
medirlo antes** (REGLA #2): si el join exacto cubre todo, meter un modelo es puro
costo y un riesgo nuevo. Por eso el diag cuenta `por_ficha` aparte — el número
decide, no la intuición.

#### Y el número decidió: **`sin_pata` pasó de 8 a CERO**

    sembrada          12   60,0%
    solo_en_primary    0    0,0%
    por_ficha          8   40,0%   ← los que ninguna regla de nombre encuentra
    sin_pata           0    0,0%

**El join exacto cubrió el 100%.** Los 8 que el sistema daba por «no tiene pata en
dólares» la tenían: los 6 BOPREAL más NDT25 (→ `NDT5C`) y SFD34 (→ `SFD4C`), que
tampoco siguen ninguna convención de sufijo.

Y con eso queda contestada la pregunta del LLM **con un número y no con una
opinión**: no hay una sola ficha que no empareje exacto, así que un modelo no
tendría ningún caso que resolver. Si algún día aparece uno, el diag lo va a
mostrar como `sin_pata` — y recién ahí se discute.

#### El bug de esa misma corrida: emparejar bien y elegir mal

Los 8 emparejaron perfecto y **los 8 devolvieron la pata en CABLE** (`BPA7C` en
vez de `BPA7D`). `hermanas_por_ficha` ordenaba por plazo y después alfabético, y
`BPA7C` < `BPA7D`. El abecedario no es un criterio de mercado.

Peor: el criterio bueno ya existía —`preferencia`, MEP antes que cable porque es
la que mira la mesa— y estaba escrito **tres veces**: ahí, una copia en la puerta
del agente y otra en el diag, estas dos con un `sorted()` alfabético. O sea que
el diag mostraba una pata y el agente iba a pedir otra.

Ahora hay UNA: `core.especies.mejor`, y las tres la usan. *Emparejar bien y
elegir mal no se ve distinto de emparejar mal* — y cable y MEP son cosas
distintas, cosa que `CLAUDE.md` ya advertía.

### 0.z EL PROBLEMA NO CIERRA CUANDO CIERRA EL MERCADO (2026-08-19)

> *«Eso tiene que ser independiente del mercado. Si ya detectó que cotiza la pata
> en pesos es lo mismo que el mercado esté abierto o no: mañana va a volver a
> abrir y va a pasar lo mismo. El agente tiene que entender que algunas cosas se
> solucionan independientemente del horario — si ya detectó el error tiene que
> saber que va a volver a pasar si no se hizo nada.»* (user)

Salió de una observación práctica —*«pensá que el mercado ya cerró, no va a tener
last price»*— y terminó destapando que **§0.u se había pasado de largo**.

Aquella sección hizo vencer los hallazgos de rueda, y estaba bien: una foto que
nadie refresca no se puede seguir afirmando. Pero el vencimiento se escribió **por
ALCANCE** (`VENCEN_EN_S = {"live": 15 min}`), así que se llevaba puesto TODO el
monitor. Y adentro del monitor hay dos familias que no se parecen en nada:

    OBSERVACIÓN DE MERCADO      «no le pusieron punta hoy» · «el precio no se
                                mueve hace 40 min». Valen AHORA. Si nadie las
                                refresca dejamos de saber → VENCEN.

    PROBLEMA DE CONFIGURACIÓN   «nadie suscribe este símbolo» · «el master apunta
                                a la pata equivocada» · «este bono no tiene
                                símbolo». Son hechos sobre NUESTROS datos. El
                                mercado no los arregla cerrando ni los cambia
                                abriendo → NO VENCEN.

**El costo no se veía, y es el que el user nombró.** El problema de configuración
desaparecía a la noche y volvía a la mañana con `abierto_at` nuevo, así que
**nunca acumulaba antigüedad**: un símbolo sin suscribir hacía tres semanas y uno
de recién se veían exactamente igual. Justo lo que uno necesita distinguir para
decidir a qué prestarle atención — y el centinela tiene un contador de `veces`
que existe para eso y que este vencimiento estaba reseteando todas las noches.

Ahora se declara **por REGLA** (`av_agent.OBSERVACIONES_DE_MERCADO` +
`av_agent.vence()`), el corte va en el `WHERE` y no después, y una regla nueva
**no vence por default** — mismo criterio que `DE_QUIEN`: el default es el lado
que NO esconde. Una regla que nadie clasificó desapareciendo sola de la pantalla
es el peor modo de falla, porque no da ningún error.

#### Y la contracara, en la puerta: qué significa «verificado» a las 17:30

Pedir la pata **sí se puede a cualquier hora** —es una fila en
`adhoc_subscriptions` y el motor la toma al abrir—, pero *«pedida, todavía sin
precio»* con la rueda cerrada invita a leerse como un resultado, y no lo es:
nadie podía dar una punta. La puerta ahora mira el reloj
(`av_agent.en_rueda()`) y contesta distinto:

    en rueda   «pedida; si pasa una rueda entera y no llega, ESA pata no cotiza»
    cerrado    «pedida. El mercado está CERRADO, así que todavía no se puede
                saber nada: la respuesta llega al abrir»

**Lo que NO cambia con el reloj es si la acción se ofrece.** `pedible` no mira la
hora: el problema es de configuración y arreglarlo a las 17:30 vale exactamente
lo mismo que a las 11.

#### Lo que la ficha empareja se puede VERIFICAR A OJO

El join es exacto, pero es una **identidad nueva**, y `NDT25 → NDT5C` no se parece
a nada. Antes de escribir una sola fila en base a eso, el diag imprime la
evidencia completa —ticker, pata propuesta, vencimiento y el `underlying`
textual— para que una persona la mire. Un emparejamiento que nadie confirmó no es
mejor que una corazonada solo porque lo hizo un `JOIN`.

### 0.aa LOS DOS PATRONES, EN LA ARQUITECTURA (2026-08-19)

> *«Hacelo bien hecho, que quede en la arquitectura, que este patrón sea general
> y no solo para esta feature y después le pase a otra cosa. Que refuerce la
> inteligencia de este modelo.»* (user)

Tenía razón: en cuatro días el MISMO error apareció tres veces, en tres módulos
distintos, y las tres se arregló el caso y no la clase. Acá quedan los dos
patrones como código reusable, no como prosa en un doc.

#### PATRÓN A — la identidad no es el nombre → `core/pareo.py`

Cada tanto hay que decir «este registro y aquel son la misma cosa» sin tener una
clave que los una. Ya pasó **cuatro** veces y las cuatro se resolvieron por
separado: las patas de un bono, el rebautizo de Aunesa (`herencia`), el emisor de
1816, y los tres lugares donde vive un símbolo.

La tentación siempre es mirar el string, y siempre falla igual, porque **el
nombre es una convención de quien lo emitió, no un dato**:

    BPOA7  →  BP[O]A7 + D  →  BPA7D      ← se cae una letra del MEDIO
    NDT25  →  NDT[2]5 + D  →  NDT5D
    AL30   →  AL30   + D  →  AL30D       ← este anda, y por casualidad

El ticker está topeado en **5 caracteres**: `AL30` tiene 4, le entra la D y la
regla de sufijo funciona; cualquier base de 5 la rompe y la va a romper siempre.
*El nombre literalmente no tiene lugar para ser la identidad.*

`core/pareo.hermanas()` empareja por FICHA —los atributos que da la fuente
autoritativa— con **las cuatro guardas adentro**, que son la mitad del módulo
porque *emparejar mal es peor que no emparejar*:

    1. solo la fuente AUTORITATIVA   la forma corta de Primary trae un underlying
                                     genérico; con ella los 6 BOPREALes comparten
                                     ficha y cada uno hereda las patas de los otros
    2. ficha COMPLETA                media ficha matchea contra todas las demás
                                     fichas incompletas
    3. tope de grupo                 una ficha que agrupa de más es genérica
    4. «no pude» ≠ «no existe»       la regla del AO29 (§0.v)

`core/especies` pasó a ser **un caso de uso**: declara qué campos forman la ficha
de un instrumento y con qué criterio se ordenan las patas, y nada más. Los 15
tests que ya existían siguen pasando sin tocar uno — la conducta es idéntica, lo
que cambió es que las guardas ya no dependen de que el próximo se acuerde.

#### PATRÓN B — dos copias sin árbitro se separan → `core/duplicados.py`

    2026-08-19  el símbolo del bono vivía en la COLUMNA y en el BLOB. El motor
                escribía leyendo el blob, la vista buscaba por la columna.
                Divergieron en 2 de 229 y esos bonos salían enteros en `--` **con
                el precio existiendo**.
    2026-08-19  `preferencia` (MEP antes que cable) estaba escrita TRES veces. Las
                tres eligieron distinto: el diag mostraba una pata y el agente iba
                a pedir otra.
    2026-08-15  el renombre dejó el blob con el significado invertido. Cuatro días
                sin que pasara nada — hasta que pasó.

El problema **no** es tener el dato dos veces: a veces hace falta (un blob que
leen 500 lugares no se migra de un día para el otro). El problema es **no
declarar quién manda y que nadie mire si siguen diciendo lo mismo**.

`core/duplicados.DUPLICADOS` es el registro **declarado** —del esquema no se puede
deducir que dos columnas guardan «lo mismo»— y cada entrada dice qué dato es,
dónde vive cada copia, **quién es el árbitro** y **qué se rompe** si divergen.
Hoy son cuatro; sumar uno son cinco líneas y una query.

#### Y lo que lo vuelve inteligencia y no documentación: el agente lo mira

`detectar_dato_partido` corre en el job nocturno (no depende del mercado: a las
23:30 es tan cierto como a las 11) y lo canta en ENCONTRÓ como cualquier
hallazgo. **`alta` sin dudar**: si dos copias difieren, algo está leyendo el valor
incorrecto ahora mismo — lo único que no sabemos es quién.

Lo que lo hace imposible de ver a mano es justo lo que lo hace peligroso: **cuando
dos copias se separan no falla nada.** No hay excepción, no hay log, cada mitad
sigue siendo coherente, y el sistema contesta con seguridad usando la equivocada.

⚠️ **NO se automatiza, y es una decisión.** Elegir la del árbitro y pisar la otra
parece obvio y no lo es: puede que la equivocada sea la del árbitro, y pisar
**borra la evidencia de que hubo una divergencia**. El agente muestra los dos
valores y decide una persona.

Y lo que no se pudo chequear **se canta** (`no_pude_chequear`, media): un
duplicado sin mirar se leería igual que uno sano, que es exactamente la forma de
mentir que estos dos módulos persiguen.

#### LA PRIMERA CORRIDA: encontró dos, y el AO29 seguía partido

    ⚠ 2 dato(s) partido(s)
       simbolo_columna_vs_blob      2 casos
       simbolo_master_vs_especies   1 caso

**El bug que tardó cuatro días en descubrirse apareció en la primera pasada.** Y
con un matiz que importa: `_ALIAS_DEL_BLOB` (§0.u) hizo que los LECTORES se
pongan de acuerdo —la columna gana al leer— pero **el dato sigue partido en la
base**. El parche es una capa de traducción, no un arreglo: cualquier cosa que
lea `data` sin pasar por `curvas_sql` todavía se lleva el valor viejo.

O sea que la divergencia estaba viva, nadie la veía, y el sistema andaba bien por
un parche que había que recordar. Exactamente la clase de deuda que este detector
existe para no dejar acumular.

#### Y NO TODOS SE ARREGLAN IGUAL — eso también se declara

Sincronizar dos copias parece siempre lo mismo y no lo es. Cada duplicado declara
si tiene arreglo **mecánico** y, si no, por qué:

    simbolo_columna_vs_blob        UPDATE  → se le escribe al blob el valor de la
                                            columna. NO es un renombre: la clave
                                            sigue llamándose `ticker` (la leen
                                            ~500 lugares), solo cambia el valor.
    ticker_corto_columna_vs_blob   UPDATE  → ídem.
    simbolo_master_vs_especies     NO      → el motor arma su universo AL
                                            ARRANCAR: el UPDATE se verifica en
                                            verde y la pantalla no cambia hasta la
                                            noche (§0.v).
    emisor_curvas_vs_assets        NO      → el árbitro es 1816 y quien escribe
                                            las dos tablas es `jobs.ficha_1816`.
                                            Escribir a mano dejaría las copias
                                            coincidiendo en un valor que ninguna
                                            fuente respalda — peor que la
                                            divergencia, porque además la esconde.

`scripts/fix_dato_partido` (dry-run por default, scopeado al WHERE de la
detección, idempotente) aplica solo los mecánicos y **releé al final**: «apliqué»
no es «pasó». Los otros los nombra y no los toca.

### 0.ab EL AGENTE MANDA MENSAJES — y después chequea si sirvió (2026-08-19/20)

Dos pedidos del user que resultaron ser el mismo: *«necesito que el agente mejore
lo de enviar mensajes al resto de usuarios… debería ser una feature de
ENVIAR_MENSAJE»* y *«necesito que este agente entienda cuándo hizo algo bien no
solamente porque yo le puse acertó, si no porque al otro día puede detectar que
los cambios realmente tuvieron consistencia»*.

Son la misma cosa porque las dos preguntan lo mismo: **¿llegó de verdad?** Un
mensaje que nadie ve y un arreglo que se deshace solo fallan igual de callados.

#### (1) MANDAR es una capacidad, no un pedazo de otra cosa

Estaba metido adentro del control de carteras sin nivel 1: para avisar de otra
cosa había que copiar el código. Ahora vive solo en
**`api/services/av_agent_mensajes.py`** y cualquier detector o job lo usa:

    directorio()   quién puede recibir            enviar()        uno
    de_quien_es()  a qué operador le toca         enviar_tabla()  con grilla
    existe()       ¿ese mail está dado de alta?   enviar_muchos() en lote

`MAX_DESTINATARIOS = 60` es un freno a propósito: un lote más grande no es un
aviso, es un incidente.

**Y no llegaba por TRES bugs encadenados**, que es la parte que vale la pena
recordar:

  1. el índice único ignoraba a **quién** iba dirigido → el segundo destinatario
     pisaba al primero en silencio;
  2. la escritura devolvía «0 filas» y nadie miraba ese 0;
  3. **la ruta `/api/avisos` no existía en el frontend.** Ese era el de verdad:
     `MisAvisos` nunca había funcionado y el `catch` se comía el 404.

La lección no es «había bugs». Es que **los tres eran mudos**: cada capa daba OK
por su cuenta. Por eso ahora la escritura mira lo que devuelve y hay un test que
exige que la ruta exista.

#### (2) El aviso de SALDOS — la primera cosa que sabe contar

Todos los días hábiles 16:45, cada operador recibe los saldos de SUS comitentes
(`jobs/saldos_a_operadores.py`). Cuatro decisiones:

  · **La fuente es la MISMA que la pantalla.** El job no tiene una sola query
    propia: llama a `titulos_negativos.saldos_del_dia()`, que es lo que sirve la
    vista SALDOS DE CUENTAS — con sus cuentas ocultas, sus excluidas y su último
    día real. La primera versión sí tenía query propia y ya divergía en tres
    cosas (usaba `current_date` en vez del último día cargado, no sacaba CDC/OTC,
    tenía su propio mínimo). *Un aviso que contradice a la pantalla no avisa: abre
    una discusión sobre cuál de los dos miente.* Hay un test que falla si al job
    le vuelve a aparecer un `cur.execute`.
  · **Cuatro cuadrantes, top 5 cada uno**: ARS a la izquierda, USD a la derecha,
    positivos arriba y descubiertos abajo. Es un aviso para actuar, no un reporte:
    lo que importa son las puntas.
  · **ARS y USD, nada más.** USDL (link) y USDC (cable) son otra cosa y sumarlos
    adentro de la columna USD mezclaría peras con manzanas. **No desaparecen**: se
    cuentan aparte y el aviso lo dice, igual que dice cuántas quedaron fuera del
    top. Truncar en silencio se lee como «esto es todo lo que hay».
  · **El título cuenta lo que la tabla MUESTRA.** Decía «46 en descubierto» arriba
    de cinco filas porque contaba todo lo que había llegado. Un título que no
    cierra con lo de abajo hace dudar de los dos.

El operador **tilda fila por fila** y eso persiste con su hora. Vale un día: al
abrir el mercado siguiente los saldos son otros y el tilde de ayer no significa
nada.

**El modal se abre para ACTUAR, no para leer** (user, 2026-08-20). Arriba van dos
líneas y nada más —quién manda y qué pasa— y abajo, directo, las tablas:

    AV AGENT — TENÉS UN MENSAJE NUEVO!
    Tenés estos saldos y el mercado ya cierra.

Antes había tres bloques de texto antes de la grilla: un título que contaba («46
cuenta(s) tuyas EN DESCUBIERTO»), el contador de pendientes y un párrafo
explicando cómo usar la tabla. **Los números no se borraron: bajaron al pie**,
que es donde se miran cuando ya se decidió algo. Lo que se sacó es el texto de
arriba, no la información — el detalle sigue diciendo cuántas quedaron afuera y
por cuál de los dos motivos.

#### Y TIENE QUE APARECER SOLO — el reloj no alcanza (2026-08-20)

*«Hay que actualizar la página, es decir inviable… hay gente que deja esto de
fondo.»* Había un poll de 5 minutos y aun así el aviso no salía hasta recargar.

**El motivo no es la app: el navegador frena los timers de una pestaña que está
en segundo plano.** Chrome los baja a uno por minuto y, pasado un rato sin
mirarla, puede congelarlos del todo. O sea que **justo en el caso que importa**
—la app abierta atrás toda la tarde— el reloj es lo primero que deja de andar.

Regla que queda, y no es solo de este componente: **para algo que tiene que
llegar, el reloj es el respaldo, no el mecanismo.** Se despierta por EVENTO —
volver a la pestaña, volver a la ventana, recuperar internet, el atrás del
navegador— y mientras está oculta no pide nada: el timer no iba a correr igual,
así que en vez de pelearle al navegador se apaga y se recupera al volver. Sale
más barato en requests que el poll de antes **y llega antes**.

Y con la app de fondo lo único que se ve de una pestaña es su TÍTULO: ahí va una
marca `(!)` mientras haya algo esperando, que se saca sola al volver.

#### (3) SEGUIMIENTO — el tiempo como evidencia

`api/services/av_agent_seguimiento.py`. Cuando algo se marca como hecho, se
**anota**; y durante `DIAS_DE_PRUEBA = 5` se vuelve a mirar si el hallazgo
reapareció. Si no volvió, el voto se emite solo con origen `verificado`.

Eso agrega un tercer origen al eval set, y **el origen importa**:

    humano      alguien miró y dijo si la causa era la correcta
    derivado    alguien aplicó la acción propuesta (aprobación, no verificación)
    verificado  pasaron los días y el problema no volvió

La compuerta de autonomía cuenta **`humano` + `verificado`** y deja afuera a
`derivado`: aprobar una acción es decir «probemos», no «funcionó». Meterlos en la
misma bolsa haría subir el número justo cuando menos evidencia hay.

Dos guardas: `revisar(None)` —«no pude mirar»— devuelve error y **nunca** cambia
un veredicto (§0.v: no se concluye «no existe» desde una consulta que no corrió),
y la recurrencia queda anotada, así que si el mismo error vuelve el agente lo
sabe en vez de descubrirlo de nuevo.

### 0.ac LOS LOGS DE LOS MOTORES (2026-08-20)

*«Es fundamental que el agente tenga presente los logs de los motores
constantemente»* (user, 2026-08-19).

**Lo que el agente ya sabía y lo que no.** `motor_caido` (§0.r) mira si el motor
PRODUCE: si su tabla de salida dejó de escribir dentro de su ventana, lo canta.
Eso deja afuera al motor que **produce y a la vez se está rompiendo** —
reconexiones, suscripciones rechazadas, excepciones que alguien atrapó y siguió.
Nada de eso llega a la base: vive en el log y nadie lo lee.

#### La lectura estaba escrita, en el lugar donde nadie podía usarla

`journalctl` ya se leía… **adentro de `api/routers/manager/logs.py`**, o sea que
la única forma de mirar era que una persona abriera la pantalla. Un service no
importa un router (regla de capas), así que para el agente la única salida
habría sido copiar el `subprocess` — dos formas de leer lo mismo, que se separan
solas (REGLA #9).

Ahora la lectura vive en **`api/services/logs_sistema.py`** y el router es un
cliente más. Lo que el router devuelve **no cambió un nombre de campo**: la
pantalla de LOGS lee `ts_epoch`/`priority`/`servicio`/`message` y renombrarlos la
habría dejado en blanco sin que fallara nada. Hay un test que los congela.

#### Los últimos N renglones no sirven para vigilar

Un motor que se reconecta mil veces deja mil líneas casi iguales; las últimas 20
son la misma. La pregunta útil no es «¿qué dijo recién?» sino **«¿qué viene
diciendo, y cuántas veces?»**.

Por eso se **normaliza**: se le sacan al mensaje la fecha, la hora, el símbolo,
el id y los números — lo que cambia en cada repetición y no ayuda a identificar
el problema — y queda la FORMA de la frase, que es su identidad real. Mil líneas
colapsan en un patrón con su cuenta. Un traceback se agrupa por su **última**
línea (la que nombra la excepción): agrupar por la primera daría un grupo por
traceback y el resumen tendría el mismo largo que el log.

Y cada grupo guarda **la ventana**, no solo la cuenta: *300 repeticiones en dos
minutos es un motor peleando contra algo; las mismas 300 repartidas en un día son
ruido de fondo* — y no se responden igual.

#### Todavía NO avisa solo, y eso es la decisión, no un pendiente

Para que el agente cante un problema hay que fijar umbrales: cuántas veces, de
qué nivel, en cuánto tiempo. **Ninguno de esos números se puede elegir sin haber
mirado nunca los logs de producción** (REGLA #2), y un detector mal calibrado
grita todos los días hasta que alguien lo silencia — peor que no tenerlo, porque
además enseña a ignorar la pantalla donde vive.

Así que primero se mide: `python -m scripts.diag_logs_motores` imprime el reparto
por motor, lo grave aparte (aunque haya salido una sola vez) y los patrones más
repetidos con su ventana. Con esos números se elige el umbral, y recién ahí el
detector.

**Y si no se puede leer, se dice.** En un host sin systemd, o sin permiso sobre
el journal, devolver lista vacía se lee EXACTAMENTE igual que «no hay errores».
Es el mismo modo de falla de §0.s y el que más caro sale: la vigilancia diría
verde para siempre. `disponible: False` con el motivo, y el diag lo imprime como
lo que es — «no sé», no «está todo bien».

#### LA PRIMERA CORRIDA DIO CERO — y el cero era el detector (2026-08-20)

14 motores, 24 horas, **ninguna línea de warn o peor**. Sospechoso, y al medirlo
—en el repo, sin tocar el Droplet— eran **tres capas tapando lo mismo**:

  1. **Ninguna unit de systemd declara nivel** (`SyslogLevelPrefix`), así que
     **journald marca TODAS las líneas como `info`**, también las de
     `logger.error`. Pedirle `-p warning` devuelve vacío *siempre*.
  2. **9 de 13 motores formateaban con `"%(asctime)s %(message)s"`** — sin el
     nombre del nivel. El texto tampoco lo decía.
  3. **`engines/valores.py` (motor_rofex) no configuraba logging en absoluto.**
     Sin `basicConfig` el logger raíz queda sin handlers y en WARNING: sus
     `logger.info` se **descartaban** y sus `logger.error` salían por el handler
     de último recurso, a stderr y sin fecha. Es el feed de precios de la mesa.

Juntando las tres, **un error de un motor era indistinguible de una línea
normal** — para el agente y para una persona leyendo `journalctl`. No es que
faltara un detector: *no había forma de encontrar un error aunque lo buscaras a
mano*. Y como un log ilegible no falla, esto podía durar para siempre.

**Las dos mitades del arreglo:**

  · **El nivel viaja EN EL TEXTO** (`core/logs.py`, formato único con
    `%(levelname)s` y `force=True`). Es lo único que sobrevive a journald, a
    `tail`, a un `grep` y a un copiar-pegar en un chat.
  · **El lector no le cree a journald**: saca el nivel del texto y se queda con
    **lo peor** entre ese y el de journald — hay servicios que sí mandan el nivel
    de verdad, y creerle solo al texto sería cambiar un punto ciego por otro. El
    filtro va como `--grep` del lado del servidor: sin eso habría que traerse 24 h
    de logs de 14 motores para descartar el 99%.

Dos detalles que hacen que esto no vuelva a esconderse:

  · Las palabras se buscan **en MAYÚSCULAS**, que es lo que imprime
    `%(levelname)s`. Sin eso, «0 errores» y «sin warnings» —las líneas que dicen
    que todo salió bien— entrarían como problemas y el detector nacería gritando.
  · El diag arranca con una **muestra de las últimas líneas de cualquier nivel** y
    dice cuánto tiempo cubren y cuántas declaran su nivel. Eso separa de una las
    dos lecturas posibles de un cero: *«están tranquilos»* de *«no se puede
    encontrar nada»*. **Un cero sin esa muestra no prueba nada.**

⚠️ **El formato nuevo NO se ve hasta reiniciar los motores**, y el deploy no los
toca a propósito. El cron los prende y apaga de lunes a viernes, así que entra
solo en el próximo arranque. Hasta entonces el diag lo canta: *«ninguna línea
dice su nivel: este cero no prueba que no haya errores, prueba que no se pueden
encontrar»*.

#### EL DETECTOR, CALIBRADO CON LA MEDICIÓN (2026-08-20)

Con el lector arreglado, 24 h sobre 14 motores dieron **171 líneas en 6 patrones**:

    ×76 en 3 min    motor_cedears     REST exception JSONDecodeError
    ×91 en 6.7 h    motor_options     Expiries configuradas ya vencidas
    ×1              motor_portfolio   ERROR símbolo inexistente, purgo y sigo
    ×1 ×1 ×1        varios            warn sueltos, todos auto-resueltos

Y ahí se ve lo que no se podía saber antes de medir: **la cuenta sola no
alcanza.** 76 y 91 son números parecidos y son dos problemas distintos — *76 en
tres minutos es algo rompiéndose ahora en loop; 91 repartidas en siete horas es
una configuración rota desde hace días que nadie mira*. Por eso son **dos reglas
con nombres propios** y no un umbral con dos valores:

| regla | cuándo | severidad |
|---|---|---|
| `rafaga` | ≥30 veces en ≤15 min | alta |
| `machaca` | ≥20 veces en la ventana | alta si es error, media si no |
| `error_de_motor` | nivel error o peor, aunque sea una vez | media |
| `no_pude_leer` | el journal no se pudo leer | media |

**Los warn sueltos se descartan a propósito.** En la medición eran tres y los
tres se anunciaban resolviéndose solos («reconectando (intento 1)», «purgo y
resuscribo sin ellos»). Reportar eso enseña a cerrar la pantalla sin leerla, y
con ella se van los avisos que sí importan. El diag los sigue mostrando cuando
alguien va a buscarlos.

Resultado sobre esos mismos datos: **6 patrones → 3 hallazgos.**

Dos cosas que salieron de escribir los tests con los casos reales, y que no se
habrían visto de otro modo:

  · **`WARNING · … REST status=ERROR` se clasificaba como ERROR.** La palabra
    estaba en el *cuerpo* del mensaje, no en el nivel. El nivel es un PREFIJO —lo
    pone `%(levelname)s` después del timestamp— y buscarlo suelto confunde *el
    nivel del mensaje* con *el tema del mensaje*. Ahora va anclado. Misma familia
    que «0 errores», encontrada con datos de producción.
  · **La forma clasifica, el nivel pesa.** La primera versión probaba
    `rafaga`/`machaca` antes que el nivel, así que un ERROR repetido 40 veces
    caía en `machaca` con severidad **media**: el que más repetía era el que
    menos se veía.

Y de yapa, al reescribir el guardián de reglas (pasó de un regex sobre
`_hallazgo(...)` a recorrer el AST) apareció que **`tabla_quieta` emitía
`sin_escribir` sin declararla**: sus votos se contaban sin poder decir por qué
causa acertó. El regex solo veía UNA de las formas de escribir un detector, así
que los cinco que arman el dict inline pasaban en verde sin haber sido mirados.

### 0.ad LOS DE AFUERA SE CAEN (2026-08-20)

*«Esto es una funcionalidad que la vi de milagro… sí o sí el agente tiene que
detectar cuándo esto está caído, avisar y dar el motivo exacto»* (user, con
Aunesa devolviendo HTTP 500 en su login mientras lo escribía).

**Lo que fallaba no era la detección.** La vista de Tesorería ya captura el error
de Aunesa, degrada bien —arma la vista con lo que hay y dice qué falta— y muestra
el mensaje exacto en un cartel rojo. Está bien hecho. Lo que falla es **cuándo**:
ese cartel existe *solo mientras alguien tiene la pantalla abierta*. Si nadie
entra, el back office puede pasar la mañana entera creyendo que el saldo del día
está completo cuando le falta la mitad.

Es el mismo patrón que ya se corrigió con SALUD (§0.l), con la latencia (§0.q) y
con los detectores que solo imprimían en el log (§0.t): **una señal que te espera
no es un aviso**.

#### No se pregunta: se deja rastro

La tentación es pegarle cada 5 minutos a cada proveedor. No, por dos razones:

  · **1816 cobra por llamada.** Un health check cada 5 minutos se come la cuota
    del día antes del mediodía.
  · **Un health check puede mentir.** Un proveedor que contesta el ping y
    devuelve 500 en el endpoint que usamos de verdad sale VERDE.

Así que al revés: **cada llamada real deja su rastro** (`core/proveedores.anotar`,
llamado desde adentro del cliente HTTP). Los daemons ya le pegan a Aunesa todo el
tiempo, así que una caída queda registrada en segundos, sin una sola llamada
extra y con el error del endpoint que importa.

Se escribe **solo cuando falla**, y como mucho una vez por minuto por proceso: un
proveedor sano no cuesta ni una escritura, y una caída de una hora no son miles
de filas diciendo lo mismo. La recuperación sí se escribe — es lo que apaga el
aviso rápido en vez de esperar a que venza.

⚠️ Y **el hallazgo VENCE** (20 min). Nadie apaga el registro cuando el proveedor
se recupera: simplemente dejan de anotarse fallos. Sin ventana, un 500 de la
semana pasada seguiría en pantalla para siempre (la lección de §0.u).

#### El aviso dice TRES cosas, y las tres hacen falta

    1. QUIÉN se cayó       Aunesa (el custodio)
    2. QUÉ deja de andar   Tesorería sin los movimientos del día…
    3. EL MOTIVO EXACTO    HTTPError: 500 Server Error for url: …/login

La 3 es la que lo hace accionable: sin el error textual no se distingue *«se cayó
el proveedor»* de *«se nos vencieron las credenciales»*, que se resuelven en
lugares distintos y por personas distintas. La 2 es la que evita que el que lo
lee tenga que averiguar si eso le arruina el día — por eso cada proveedor declara
su `rompe` en el catálogo, y hay un test que lo exige.

#### DESDE Y HASTA QUÉ HORA el problema es real

Pedido del user en la misma corrida: *«es fundamental entender desde qué hora
hasta qué hora el error es real para cada motor»*. Y al mirarlo apareció un
hueco: **`diagnostico._estado` devuelve `sin_datos` ANTES de mirar la ventana**,
así que un motor de mercado que nunca escribió salía en ALTA a las 3 de la mañana
y los sábados. `fuera_rueda` ya estaba cubierto; éste no, porque nunca llegaba a
compararse contra un umbral.

Ahora cada pieza viaja con su `ventana` y **el hallazgo lo dice en palabras**:

    CUÁNDO ES REAL: corre de 10:00 a 17:05 ART, de lunes a viernes;
                    ahora son las 14:32 ART y está DENTRO de su ventana:
                    no es que esté apagado.

El horario se **lee** de `diagnostico._APERTURA`/`_CIERRE`, no se escribe a mano:
un «10 a 17:05» tipeado en el texto es una segunda verdad que se desactualiza
sola el día que muevan el horario del mercado (REGLA #9), y hay un test que lo
impide.

#### Y otro reloj doble, encontrado por los tests

`_recien_abrio` **decía en su docstring** que la hora sale del árbol y el código
llamaba a `ahora_ar()`. Dos relojes para juzgar UNA foto. Se descubrió porque
tres tests fallaban **solo entre las 10:00 y las 10:30 ART** — media hora por día
de rojo intermitente sin causa aparente. Es el mismo bug que tenía
`salud._chequeo_job` y por el mismo motivo, así que la regla ya se puede escribir
sola: **cuando algo se evalúa contra una foto, el tiempo tiene que salir de la
foto**.

### 0.ae LLAMAR, BARRER Y AVISAR — y el veredicto que se contradecía (2026-08-20)

Tres pedidos del user en la misma caída de Aunesa, y un bug propio en el medio.

#### (0) EL BUG QUE NOS DEJÓ A NOSOTROS SIN AUNESA

Una guarda nueva exigía `AUNESA_CLIENT_ID` antes de hacer el login. **Esa
credencial va vacía y siempre fue así.** El login cortaba antes de tocar la red y
se caía todo lo que depende del custodio —Tesorería, saldos liquidados, tenencia
del día, informes— con «faltan credenciales», apuntando al lugar equivocado.

Y el modo de falla es el peor de todos: **no fallaba nada nuevo.** Aunesa ya
devolvía 500, así que la vista ya decía CAÍDO; el cambio solo reemplazó una causa
ajena por una propia sin que se notara la diferencia.

> **La lección, y vale para cualquier credencial: una validación de config que
> nunca se probó contra la config REAL es una hipótesis, no una guarda** (REGLA
> #2). Que un campo se llame `clientId` no significa que el proveedor lo pida, y
> meses de logins exitosos con el campo vacío son la medición que manda.

#### (1) EL VEREDICTO SE CONTRADECÍA A SÍ MISMO

En la misma corrida, el diag imprimió:

    paso 2   →  «5xx con el body VACÍO: es su servidor. Nada que tocar de este lado.»
    VEREDICTO →  «es NUESTRO — faltan credenciales en el .env del Droplet.»

**El mismo informe afirmando las dos cosas**, y mandando a revisar una
configuración que siempre había sido así. Es exactamente el bug de OLC3O (§0.g),
que es la familia más cara: no da error, se lee con seguridad, y quema el tiempo
de quien lo sigue.

La regla que quedó es de lógica pura: **si el host devuelve 5xx a una request SIN
credenciales, se rompió ANTES de leerlas.** Ninguna revisión de nuestro `.env`
puede explicar eso. La evidencia le gana al checklist, y un chequeo de config
solo puede ser la causa si el host contesta 4xx.

#### (2) LLAMAR: «si el error es 500 es porque está caído»

*«El agente sí o sí tiene que poder llamar a Aunesa para ver la conexión y
entender el error»* (user). No contradice al rastro pasivo (§0.ad), lo completa:
el rastro dice QUE falló, con el error de una llamada real; la prueba contesta la
pregunta que sigue, **¿es de ellos o es nuestro?**

Tres reglas, las tres para no hacer daño: **nunca manda credenciales** (un login
fallido repetido bloquea la cuenta — y justamente porque no las manda, un 5xx
prueba que se rompió antes de leerlas), **un solo intento** (es un diagnóstico,
no un reintento) y **solo cuando ya hay una falla anotada** — nunca en el camino
feliz, porque 1816 cobra por llamada.

El código se interpreta con **el contrato que publica el custodio** (200/204
anda · 400/401/403 anda y rechaza, que es lo correcto sin credenciales · 500 es
error interno suyo). Sin eso, un 403 y un 500 se leen igual: «no anda».

Y un detalle que resultó ser diagnóstico puro: Aunesa documenta que un 500 vuelve
como `{"errors":[{title,detail}]}`. **Cuando en vez de eso llega el HTML de
Tomcat, se rompió antes de llegar a su propio manejador de errores** — no es una
condición prevista por su aplicación, es su aplicación caída. Es la diferencia
entre «me rechazaron» y «se les cayó».

#### (3) BARRER: ¿le pasa a las cinco APIs o a una sola?

*«Que intente conectarse a todas las APIs que usamos, ya que esto impacta en
muchos lados, y darme un análisis general»*. Una sola prueba no contesta eso, y
la diferencia decide qué hacer:

| resultado | qué significa |
|---|---|
| falla el LOGIN | **todo Aunesa bloqueado** — sin token no entra un dato de ninguna. No hace falta probar las cinco |
| fallan las 5 | su **servicio entero**. No hay nada que arreglar de este lado |
| falla 1 de 5 | un endpoint suyo. **El resto sigue entrando** — y hay que decirlo, o el equipo da por perdido el día |

Las cinco son las que el sistema usa de verdad (padrón, movimientos del día,
tenencia, boletos, saldos liquidados), con un test que lo exige: si falta una, el
análisis diría «anda todo» sobre algo que nadie probó. Y el barrido tiene freno
(15 min): son 6 requests y el monitor corre cada 5 minutos — sin freno, una caída
de una hora son 72 requests contra un servicio que ya está mal.

#### (4) AVISAR DIRECTO, no solo «encontrar»

*«Esto lo tiene que avisar directamente además de encontrar»*. Y la razón es
concreta: **ENCONTRÓ es admin-only**, y el que sufre que Aunesa esté caído es el
back office, que ni ve esa pantalla.

Va a los que escriben en Tesorería (`operaciones.tesoreria_escritores`) **más los
admin** — no una lista nueva: es la gente ya habilitada a operar justo lo que se
rompe, así que se mantiene sola cuando cambia el equipo. Los admin siempre, para
que una allowlist vacía no deje el aviso sin destinatario justo cuando más
importa; y si aun así no hay a quién, **se dice** (un «enviados: 0» silencioso se
lee igual que «no hacía falta avisar»).

**Un aviso por día y por proveedor**, no por corrida: el monitor corre cada 5
minutos y el 84º mensaje idéntico informa menos que el primero. El análisis
general va PRIMERO en el cuerpo — «fallan las cinco» es lo accionable; el
traceback es el respaldo.

### 0.af QUIÉN DEPENDE DE QUIÉN — tres avisos, un solo problema (2026-08-20)

*«Los jobs, ¿a dónde apuntan? Ej: a Aunesa… ¿Aunesa está caído? Listo, avisar que
dio error PORQUE está caído Aunesa. Adelantarte: no solamente avisar, sino que el
aviso sea con más contexto»* (user).

**El agente ya veía las dos cosas y no las relacionaba.** En la misma pantalla:

    proveedor_caido   Aunesa no responde
    salud_job         portafolio_diario: la última corrida falló   ×80
    salud_job         tenencia (snapshot SQL): la última corrida falló

Tres avisos y un solo problema. Para atar el cabo hay que saberse de memoria que
`portafolio_diario` le pega a Aunesa — y el que no lo sabe sale a buscar un bug
que no existe, en la peor hora.

**Cómo se llama esto**: correlación por dependencias (*root-cause correlation*).
En criollo: **el agente sabe de qué depende cada cosa, así que cuando algo se cae
agrupa todo lo que se cayó por eso y avisa una vez, con la causa.**

#### La dependencia SALE DEL CÓDIGO, no de una lista

Un job que le pega a Aunesa lo dice **en su `import`**. Esa dependencia ya está
escrita: `core/dependencias` lee el árbol de sintaxis y la deriva. Misma ley que
el resto del contexto del agente (§0.r) — *una lista a mano se queda vieja el día
que alguien agrega un job y no se acuerda, **y no avisa***.

Dos cosas aparecieron al estrenarlo, y las dos eran reales:

  · **`core.bcra` no existe** (es `bcra_api`), así que la dependencia del BCRA no
    se detectaba **nunca**. Un catálogo que nombra un módulo inexistente no da
    error: **da silencio**. Hay un test que lo cruza contra el repo.
  · **Cuatro módulos le hablan a Aunesa por fuera del cliente único** (`jobs/aum`,
    `jobs/cashflow`, `jobs/sync_comitentes`, `api/services/aunesa_negocio`), con
    su propio `requests`. Sus fallas **no dejan rastro** (§0.ad) y por el import
    no se los puede relacionar. Migrarlos es otro trabajo; mientras tanto la
    dependencia se detecta **por el host que mencionan**: el import es una pista,
    la URL es otra, y las dos están escritas.

Se sigue **un solo salto** de indirección. Con dos, todo depende de todo
(cualquier módulo llega a `core.postgres`) y la correlación empieza a inventar.

#### Qué cambia un hallazgo que es CONSECUENCIA

| | qué pasa | por qué |
|---|---|---|
| el motivo | «…**porque Aunesa no responde**» | es lo único que se pidió, y ahorra la búsqueda inútil |
| la severidad | baja un escalón, **no se apaga** | apagarlo sería mentir (el dato falta igual); dejarlo en ALTA junto a la causa muestra tres incendios donde hay uno |
| la causa | «y por esto fallaron **otras 4** piezas» | ese número **es** el impacto, y decide si se llama al custodio ahora o se espera |

⚠️ **Y NO SE INVENTA UNA CAUSA.** Solo se relaciona cuando la dependencia está
escrita **y** el proveedor está caído en la misma ventana; los hallazgos de bonos
nunca son consecuencia de un proveedor. **Atribuir de más es peor que no
atribuir**: un job que falla por su propio bug, archivado como «culpa de Aunesa»,
es un bug que nadie va a arreglar nunca. Congelado por test — `job:bcra` con
Aunesa caído tiene que quedar intacto.

#### Y de paso: 14 casos explotaban, y un TEST EXIGÍA EL BUG

El masivo #6 reportó `TypeError: diagnosticar() got an unexpected keyword
argument 'con_ia'` en **14 casos** — todos los chequeos de SALUD y todos los
controles, la categoría entera. El parámetro se había ido al dar de baja la lente
con IA y la llamada quedó pasándolo.

Lo que lo mantuvo vivo es lo interesante: **había un test que lo exigía.**
`test_la_lente_con_IA_va_apagada_en_masivo` hacía `assert "con_ia=False" in src`
— congelaba una intención mirando un STRING. Cuando el parámetro desapareció de
la firma, el test siguió pidiendo que la llamada lo pasara, y siguió en verde.

> **Un test que verifica un texto puede sobrevivir a la cosa que verificaba.**
> Ahora chequea lo estructural (que la función no tenga por dónde gastar un
> token), que es lo que se quería decir y no se puede cumplir de mentira.

### 0.ag EL AVISO VA CORTO — y ahora hay un test (2026-08-20)

El user, por segunda vez: *«Te recuerdo lo del texto y las palabras raras
—“custodio”—. Si está caído Aunesa decí **AUNESA CAÍDO + motivo simple** y listo.
Nada de palabras ni tanto texto, con la hora de actualización. Lo mismo para
todo»*.

Antes:

    Aunesa (el custodio) no responde (7 intentos)
    QUÉ DEJA DE ANDAR: Tesorería se queda sin los movimientos del día (ingresos,
    egresos y saldo final incompletos), y no se actualizan los saldos…
    MOTIVO EXACTO: HTTPError: 500 Server Error:  for url: https://aca.aunesa…
    ANÁLISIS: Fallan las cinco: es su servicio entero, no un endpoint. No hay…

Ahora:

    AUNESA CAÍDO · 500 Server Error
    Se cae: Tesorería, saldos, tenencia e informes. Lo cargado a mano sí está.
    Fallan las 5: es su servicio entero. Hay que avisarles.
    Falló hace 3 minutos, 7 veces · último OK 20/08 11:58

**Se convirtió en test porque se pidió DOS veces.** Una preferencia que se
repite dejó de ser una preferencia: es un requisito, y los requisitos que solo
viven en un chat se pierden en el commit siguiente. `test_avisos_cortos.py` mide
las dos cosas que arruinan un aviso y **sí se pueden contar** —el largo (título
≤90, sin párrafos en el cuerpo) y las palabras raras— sobre hallazgos armados con
datos reales de producción, no de ejemplo.

Tres detalles del camino:

  · **`_corto` era demasiado ingenioso.** Partía el error por «:» y elegía «el
    primer pedazo que no hablara de una url»; con `500 Server Error for url:
    https://…` elegía **`https`**. Se resolvió cortando por delante (el tipo de
    excepción) y por detrás (la URL), que es lo que un humano hace al leerlo.
  · **Los tests viejos fijaban el texto largo** y había que re-apuntarlos. Vale
    la pena decirlo: un test que congela una frase se rompe cada vez que se
    mejora la redacción — por eso los nuevos miden LARGO y PALABRAS, no frases.
  · **El buscador de palabras raras se comía su propia documentación.** Un regex
    sobre el archivo entero matcheaba los docstrings, donde la palabra aparece
    justamente para explicar por qué no se usa. Ahora se recorre el AST y los
    docstrings se descartan **por identidad del nodo**, no por su texto:
    `ast.get_docstring` devuelve la versión ya limpiada y comparar por valor no
    encuentra el original. Tercera vez que este repo tropieza con lo mismo.

### 0.ah CUANDO ALGO VUELVE, TAMBIÉN SE AVISA (2026-08-20)

*«Quiero que el agente sepa avisar cuando un motor que estaba caído vuelve a
funcionar»* (user).

**Hasta acá se arreglaba y desaparecía en silencio.** Los hallazgos de rueda se
REEMPLAZAN en cada corrida (§0.u): si el motor vuelve, su fila simplemente no se
escribe. El que estaba esperando no se entera nunca y termina entrando a la
pantalla cada diez minutos a ver si sigue el problema. Peor cuando el aviso salió
por mensaje al back office: **quedan con la mala noticia y sin la buena**, así
que siguen operando a mano pensando que falta media jornada.

#### Sin tabla nueva

La corrida anterior **todavía está en la tabla** cuando arranca la nueva —
`reemplazar_hallazgos` borra e inserta en la misma transacción, así que hasta ese
momento lo viejo sigue ahí. Se lee antes de pisar y se resta:

    lo que estaba mal antes  −  lo que está mal ahora  =  lo que se arregló

Sin estado que mantener y sin nada que se pueda desincronizar. Hay un test que
verifica el ORDEN de las dos llamadas: si se comparara después del `DELETE` no
habría con qué comparar y el detector diría siempre «no volvió nada» **sin dar un
solo error**.

#### Qué se anuncia, y por cuánto tiempo

  · **Solo el sistema** (motores, jobs, proveedores, tablas). *Un bono que
    consiguió punta no es una noticia*: pasa cien veces por día y anunciarlo
    llenaría la pantalla de confeti hasta que nadie mire ninguna — y ahí se
    pierden también las que importan.
  · **En severidad BAJA.** Que una buena noticia aparezca arriba de un problema
    real sería justo al revés de lo que la pantalla tiene que hacer.
  · **Vence a los 30 minutos**, contra las horas de un problema: *una buena
    noticia envejece más rápido que una mala*. «Volvió hace tres horas» no le
    sirve a nadie y ocupa el lugar de lo que sí está pasando ahora. Es el tercer
    plazo de vencimiento de la vista, junto al de las observaciones de mercado y
    el «no vence» de los problemas de configuración.
  · **Dice QUÉ estaba pasando** (`Estaba: AUNESA CAÍDO · 500 Server Error`).
    «Volvió» a secas es una frase sin información: el que lo lee tiene que poder
    atarlo al aviso que vio ayer.

#### Y por el mismo canal

Si la caída se avisó por mensaje, la vuelta también — a la misma gente
(`_a_quien`, la allowlist de Tesorería + admin) y **una vez por día**. Un motor
que vuelve, en cambio, NO le escribe al back office: es del sistema y no les
toca. Mandarles lo que no es suyo es exactamente cómo se logra que dejen de leer
los mensajes.

### 0.ai UN AVISO QUE NO SE PUEDE VOTAR NO SIRVE (2026-08-20)

*«No le pone hora ni nada… si vas a decir eso, para acertar me tenés que mostrar
que falló en horarios donde debería funcionar; si no, no tiene validez. Si tenés
los logs de todo, o sea, es clarito»* (user).

Antes, la fila de ENCONTRÓ decía:

    motor_rofex (trades)   SIN PRODUCIR   motor de MERCADOS: hace rato que no produce
                                          ¿ACERTÓ?  ✔ SÍ   ✖ NO

Ahora:

    motor_rofex (trades)   SIN PRODUCIR   sin producir hace 40 min · esperado live
                                          · 14:22, en ventana

**El detalle de pantalla que lo explica todo, y que yo no había mirado: el botón
¿ACERTÓ? está en la FILA, y la fila muestra solo el `motivo`.** Toda la evidencia
—la ventana, la hora, la cadencia esperada— estaba en la evidencia, una pantalla
más abajo. O sea que se pedía un voto sobre una frase sin un solo número.

> **Un eval set alimentado así no mide la puntería del agente: mide la paciencia
> del que vota.** Y como el eval set es lo que habilita cada paso de autonomía
> (§0.f), una medición sucia acá contamina todo el roadmap.

Lo que entra en el motivo, y por qué cada cosa:

| | por qué |
|---|---|
| **cuánto hace** | sin eso no se distingue un tropiezo de algo roto desde ayer |
| **qué se esperaba** | sin el «debía», el voto lo emite solo quien ya se sabe la cadencia de memoria — al revés de para qué existe el aviso |
| **la hora + en ventana** | es literalmente lo que el user pidió: *mostrame que falló cuando debería estar funcionando* |

Y la VISTA (`MERCADOS`, `PORTFOLIOS`) salió del motivo: el nombre de la pieza ya
está en su columna y repetirlo gastaba los caracteres que necesita la prueba.

#### La prueba del log, adentro del aviso

*«Si tenés los logs de todo, es clarito.»* Un motor caído ahora trae **su última
línea de log** en la evidencia: `Último log 13:48: WebSocket desconectado`. Con
eso el voto se emite mirando, sin ir a `journalctl`. Solo para motores (un job no
tiene unidad de systemd propia) y **con freno de 10 minutos por unidad**: es un
subprocess por motor caído y el monitor corre cada 5 minutos.

#### La regla, congelada

`test_avisos_cortos.py` exige que **todo motivo de un hallazgo del sistema traiga
un número y una hora**. Es la versión chequeable de «si se pide un voto, en la
misma línea tiene que estar la evidencia». También se arregló `_humano`, que
mostraba «cada 0 min» para una tabla que escribe cada 30 s — justo el número que
hacía votable el hallazgo.

### 0.aj EL MISMO PROBLEMA, CONTADO UNA VEZ (2026-08-20)

Tres cosas que saltaron de la corrida de 59 hallazgos, y las tres son la misma
enfermedad: **el agente muestra su plomería en vez de mostrar el problema.**

#### 1) 48 de 59 seguían con el texto largo

La tijera de §0.ag se había aplicado a los avisos NUEVOS (proveedores, logs,
motores) y no a los detectores viejos, que son la mayoría de la pantalla. Cuatro
reglas pasaron por el mismo corte — `sin_punta`, `cotiza_en_pesos`,
`precio_fuera_de_escala`, `pata_equivocada`:

    antes  GD46 es de curva USD y cotiza por su pata en pesos, así que la
           grilla lo muestra al lado de bonos en dólares. El motor divide por
           el MEP (1.520,44): la valuación está bien, lo que se ve raro es la
           columna de precio. La pata en dólares ya está sembrada: «GD46D» —
           se puede pedir sin reiniciar nada.

    ahora  cotiza en pesos 102.700 · paridad real 91.4% · valuación OK · 12:51

**El párrafo no se borró: se mudó** a `evidencia.texto`, que es donde vive el
detalle. Lo que cambia es qué se lee sin abrir nada.

Y con eso entró la hora en las cuatro (`_hhmm`), que es el requisito de §0.ai
para que el ✔/✖ signifique algo. Ojo con el detalle que casi se me pasa: los
detectores trabajan en **UTC** y estampar UTC diría 15:51 cuando en la pantalla
de la mesa son las 12:51 — `_hhmm` convierte a ART siempre.

#### 2) `motor_options` aparecía DOS VECES con el mismo título

    motor_options: 91 veces en 6.7 h
    motor_options: 91 veces en 6.7 h

Eran **dos patrones distintos** de log. El título no decía cuál, así que en la
pantalla se leía como el agente repitiendo un aviso — y el que cierra el
duplicado se lleva puesto un problema real sin enterarse. Ahora el patrón entra
en el título, recortado a lo que quede de renglón, y va la hora de la última vez:

    motor_options: Expiries configuradas ya vencidas · 91 veces en 6.7 h · 20:12
    motor_options: WS reconectando sin respuesta · 44 veces en 6.7 h · 20:12

Congelado: un test falla si dos avisos del mismo motor comparten título.

#### 3) `motor_cedears` salía dos veces siendo un solo problema

Uno del árbol (`motor_caido · sin_datos`) y otro de los logs (`motor_ruidoso ·
ráfaga`). Dos detectores mirando la misma pieza, y en la lista dos motores rotos
donde hay uno.

**Y lo que se perdía partiéndolo es justo lo que sirve: el árbol dice QUE está
roto, el log dice POR QUÉ.** Juntos son accionable; separados, uno es una queja y
el otro un dato suelto. `av_agent_causas` ahora los junta —el mismo lugar donde
ya se relacionaba «el job falló porque Aunesa está caído»—: el caído suma el
motivo del log a su título y el del log baja a segundo plano marcado
`mismo_problema_que`, sin borrarse (es la prueba).

> ⚠️ **El bug que me comí escribiéndolo, y que vale más que el arreglo:**
> `_correlacionar` cortaba con un `return` temprano cuando no había ningún
> proveedor caído, así que puse la función nueva DESPUÉS de ese corte. Andaba en
> el test y no habría corrido nunca en producción — porque el caso normal, el
> 99% de los días, es que no haya ningún proveedor caído. **Una función que solo
> se ejecuta cuando además pasa otra cosa mala no está integrada: está de
> adorno.**

### 0.ak LA RESPUESTA QUE LLEGA DESPUÉS (2026-08-20)

El user, mirando la pantalla:

    BUSCAR LA PATA USD ✔ pedida · pedida; todavía sin precio — si pasa una
                        rueda entera y no llega, ESA pata no cotiza

    *«no termino de entender si lo cambio o qué… eso es SUPER INMEDIATO, no es
    ni 1 seg y ya sabe si da o no da punta. Me resulta raro. Acá le falta un
    pasito más: está bien, sí, pero no terminás de entender ni te quedás
    tranquilo.»*

Y tenía razón por una causa que no era el texto. **`verificar()` corría cero
segundos después de `aplicar()`**, y el `adhoc_watcher` del motor levanta la
suscripción recién a los 5 s. Esa frase era la ÚNICA que la función podía
devolver — siempre, para todos los casos, pasara lo que pasara.

> **Un chequeo que solo tiene una respuesta posible no es un chequeo: es un
> cartel.** Y engaña más que no verificar, porque parece que verificó.

#### Eran dos preguntas y se contestaban como una

| | quién la contesta | cuándo |
|---|---|---|
| ¿la acción hizo lo suyo? (¿quedó pedida?) | nosotros | al instante — `verificar()` |
| ¿y la que abre? (¿esa pata cotiza?) | **el mercado** | cuando quiera — `veredicto()` |

La propuesta ya no se sella `aplicada` fingiendo que sabe: queda en estado
**`esperando`**, y `av_agent_respuesta` la relee en cada pasada del monitor de
rueda hasta poder cerrarla. Dos finales, y **los dos se cantan**:

    llegó precio        → la pata SÍ cotiza · y recién ahí hay un paso siguiente
                          real (apuntar el master, que pide reiniciar el motor
                          fuera de rueda — sigue sin automatizarse, §0.u)
    se agotó la espera  → la pata NO cotiza · confirmado, deja de ser un tema

**El «no» es la mitad que importa** y es lo que el user estaba pidiendo con *«no
te quedás tranquilo»*: un no medido cierra el tema; el silencio lo deja abierto
para siempre. Por eso se cierra en OK y no en `fallida` — la acción anduvo, lo
que se confirmó es que del otro lado no hay nada.

⚠️ **La espera se mide en segundos de MERCADO ABIERTO** (`espera_s`, 45 min),
misma regla de §0.u: una pata pedida a las 16:50 no estuvo «3 horas sin punta» a
las 20:00 — estuvo 40 minutos y después cerró el mercado. Y si dejamos de
escucharla, el «no vino punta» vuelve a no probar nada (§0.v): ahí sigue
esperando en vez de cerrar.

#### Y las «4 patas» que eran 2

    Sembrada: 4 pata(s) en dólares
    BPB7C, BPB7C, BPB7D, BPB7D

No era un duplicado: son **dos patas en dos plazos** (CI y 24hs) y `_corto()`
borra justo el plazo que las distingue. Ahora se agrupan por nombre y el plazo va
al lado (`BPB7D (24hs/CI) · BPB7C (24hs/CI)`). Un nombre repetido sin explicación
hace dudar de todo lo demás que dice la pantalla, que es lo caro.

> ⚠️ **Y el error que casi cuesta caro escribiendo esto**: el módulo nuevo se
> llamó primero `av_agent_seguimiento` y **ese archivo ya existía** (§0.ac, *«el
> arreglo, ¿aguantó cinco días?»*) — se pisó entero con un `Write`. Lo cazaron
> sus propios tests en la corrida siguiente y se recuperó de git intacto, pero la
> lección queda: **antes de crear un archivo hay que mirar si está**, y los dos
> conceptos son distintos de verdad (aquél mide DÍAS y la persistencia del
> arreglo; éste mide MINUTOS de rueda y la respuesta del mercado).

### 0.al ¿EL CRON DEL REPO ES EL QUE CORRE? (2026-08-20)

Salió de una pregunta del user que parecía trivial: *«¿ya está ok el aviso de
saldos a operadores de las 16:30, el automático?»*. Buscando la respuesta
aparecieron dos cosas.

**La chica**: está a las **16:45 ART** (`45 19 * * 1-5` UTC), no a las 16:30.

**La grande**: no había forma de contestar si corrió — y hay una razón concreta
por la que podría no haber corrido nunca.

#### El agujero

`deploy/crontab.txt` dice en su encabezado que es la **fuente de verdad**, y todo
el sistema le cree:

| quién | qué hace con el archivo |
|---|---|
| `jobs_catalogo` | lo parsea: es el catálogo de jobs |
| `salud` | arma un chequeo por línea (¿corrió cuando debía?) |
| `diagnostico_registry` | valida el inventario contra él |
| tab SKILLS | de ahí saca el horario de cada detector |

**Y nadie lo compara nunca con el crontab real de la máquina.** `deploy.sh` hace
`git pull`, `apply_schema` y reinicia la API — **no instala el crontab**. O sea
que agregar un cron al repo no lo pone a correr: hay que instalarlo a mano, y si
alguien se olvida, el job no existe.

> Es **REGLA #9(B) textual**: el mismo dato en dos lugares, sin árbitro y sin
> chequeo. Y falla del modo que este proyecto ya conoce de memoria: **no falla
> nada**. El archivo está bien, el código está bien, los tests pasan, el catálogo
> muestra el job, la pantalla del agente lo lista con su horario… y no corrió.
> Se descubre cuando alguien pregunta «¿esto funcionó?», que es literalmente
> cómo apareció.

#### Qué mira, y en las dos direcciones

    en el archivo y NO en la máquina  → el job NO CORRE y todos creen que sí
    en la máquina y NO en el archivo  → corre algo que el repo no declara: nadie
                                        lo revisa, y la próxima instalación del
                                        archivo se lo lleva puesto sin avisar

Corre con los detectores del sistema (`jobs/db_tamano`, de noche) y emite
`cron_desalineado`. **Si no puede leer el crontab lo DICE** en vez de callarse:
sin eso, un `crontab` que no está en el PATH devolvería «ninguno instalado» y el
detector cantaría los 40 jobs como caídos — o peor, se quedaría mudo. Es la regla
de §0.v otra vez: *no se concluye «no existe» desde una lectura que falló*.

Se compara la ORDEN, no el archivo: comentarios, `MAILTO=` y `PATH=` quedan
afuera, y los espacios se colapsan — pero **cambiar el horario sí es una
diferencia** y hay un test que lo exige, porque colapsar de más taparía justo lo
que hay que ver.

#### Y para contestarlo hoy: `scripts/diag_crontab`

Dos preguntas distintas, las dos en una pasada: **¿está instalado?** (repo vs
máquina) y **¿corrió?** (`manager.job_runs`, con las últimas corridas y su
error). Estar instalado y haber corrido no son lo mismo: puede fallar el lock del
`run_job.sh`, el venv o el propio job.

> ⚠️ **La primera versión del diag se inventó las columnas** (`job`,
> `duration_ms`, `error`). La tabla real es `tipo`/`status`/`started_at` + un
> `data` jsonb — REGLA #2 en vivo: lo cazó mirar `sql/schema.sql` antes de
> pushear, no un test.

### 0.am LA PATA EQUIVOCADA POR FIN TIENE ARREGLO (2026-08-20)

El user, viendo los BOPREALes en la pantalla por enésima vez:

    *«estos siguen apareciendo, es algo de no creer. Necesito de una vez por
    todas que esto se solucione.»*

Y tenía razón por una causa **estructural, no de detección**: el hallazgo estaba
perfecto —nombraba el bono, la pata mala, la buena— y **no había ninguna acción
que lo arreglara**. La única puerta era `mercado.pata_dolar`, que PIDE la pata en
dólares pero **no toca `mercado.curvas.instrumento`**. O sea que el master seguía
apuntando a la pata en pesos, el detector lo volvía a ver en la pasada siguiente,
y el aviso reaparecía **todas las ruedas, para siempre**. Marcar «acertó» tampoco
lo cerraba: el agente había acertado, y aun así nadie podía hacer nada.

> **Un hallazgo sin arreglo posible no es un aviso: es una pared.** Y una pared
> que aparece todos los días enseña a ignorar la lista entera — el mismo daño que
> hacían los 46 falsos positivos de §0.u, por el camino contrario.

#### Lo que frenaba automatizarlo, y por qué ahora se puede

La objeción original era buena (§0.u): *el motor arma su universo al arrancar, así
que cambiar el campo no se ve hasta reiniciarlo fuera de rueda, y una acción que
se aplica y no se ve destruye la confianza en todas las demás*.

Se resuelve haciendo **las dos cosas en el mismo paso**:

    1. se corrige el master  → `mercado.curvas.instrumento` = la pata buena
    2. se PIDE esa pata      → `adhoc_subscriptions`, que el `adhoc_watcher` de
                               `motor_rofex` levanta en 5 s, sin reiniciar, en
                               plena rueda

Con las dos: el precio entra en el acto, la grilla lo muestra en dólares (la vista
joinea por la columna) y el master ya quedó bien para el próximo arranque. **El
hallazgo desaparece en la pasada siguiente**, que es lo único que se pidió.

`mercado.apuntar_pata` + control `patas_equivocadas`. Tres guardas:

  · **la pata sugerida la trae el control, no se adivina** — `BPOA7 → BPA7D` se
    come una letra del medio y ninguna regla de string la saca (REGLA #9 A);
  · **se exige `es_default`**, no «cualquier pata en dólares»: el cable NO es el
    MEP, y elegir mal cambia un problema por otro;
  · **el `UPDATE` tiene que tocar exactamente UNA fila**, o aborta.

⚠️ **Se escriben LAS DOS COPIAS del símbolo** (la columna y la clave `ticker` del
blob) en el mismo `UPDATE`. `curvas_sql` hace ganar a la columna al leer, así que
con una alcanzaría — pero dejar el blob diciendo otra cosa es **recrear la
divergencia que costó cuatro días**. Se arregla el duplicado, no se confía en el
árbitro (REGLA #9 B). Y si el campo queda bien pero la suscripción falla, la
acción **lo dice**: el estado a medias es real y taparlo sería prometer un precio
que no va a llegar hasta el próximo reinicio.

#### Lo que salió del diag del crontab, de yapa

  · **`seguimiento` no está instalado** — por eso `NUNCA corrió`. Es exactamente
    lo que §0.al vino a detectar, y apareció en su primera corrida.
  · **`controles_datos` moría todos los días**: importaba `core/ai_resumen`, que
    se borró el 2026-08-19 con el copiloto. Corría los 20 controles y explotaba
    con `ModuleNotFoundError` **al final**, así que calculaba todo y no
    persistía ni avisaba nada. No se reemplaza por otra IA: rige la regla de
    §0.k — *una tarea de IA existe solo si alguien lee su salida*.
  · **Y 4 de los 5 «NUNCA corrió» eran un bug MÍO**: el nombre del cron no es el
    nombre con que el job se registra (`portafolio_diario` loguea como `aum`; un
    `*_chain` corre varios módulos que loguean cada uno con el suyo). La
    resolución correcta ya existía en `jobs_catalogo` —por módulo, con un mapa de
    alias— y el diag se había hecho una copia propia. **Un diag que grita en
    falso enseña a ignorarlo**, que es la misma enfermedad que el agente vino a
    curar. Ahora delega, y cuando no encuentra corridas dice «no hay corridas con
    ese nombre», no «nunca corrió».

### 0.an EL AuM NO SE ESCRIBIÓ Y EL AGENTE NO SUPO DECIR POR QUÉ (2026-08-20)

El diag del crontab (§0.al), en su segunda corrida, trajo esto:

    aum   ✖ 20/08 11:00 error · HTTPError: 500 Server Error for url: https://aca.aunesa.co…

**`aum` es `jobs/portafolio_backfill --diario`: el writer de `portafolio.tenencia`,
la fuente única del AuM.** Ese día no escribió. Es el incidente del 2026-08-07
otra vez —el backfill falla y nadie se entera— salvo que ahora se vio.

Y lo que importa es **por qué el agente no lo cantó con su causa**, porque las
dos piezas para hacerlo ya existían. Había DOS eslabones rotos, cada uno
suficiente para romper la cadena solo.

#### 1) El fallo no dejaba rastro

`jobs/aum.py` le pega a Aunesa con **su propio `requests.Session()`** — nunca
pasa por `core/aunesa`, que es donde vive el `proveedores.anotar()`. O sea que
para el detector de caídas ese 500 **no ocurrió**: sin rastro no hay
`proveedor_caido`, y sin eso no hay nada que correlacionar.

Son cuatro los clientes sueltos y estaban declarados en el propio comentario de
`core/dependencias` como deuda conocida. Migrarlos a `core/aunesa` sigue siendo
lo correcto y es otro trabajo; lo que se hizo hoy es que **dejen el rastro**:

  · **`jobs/aum.py`** engancha `proveedores.rastrear(_SESSION)` — el hook va en
    la SESSION, no en cada llamada, así una función nueva en ese módulo queda
    cubierta sin que nadie se acuerde. El throttle de `anotar` ya evita que las
    ~1800 requests del backfill escriban 1800 filas.
  · Los otros tres usan `requests` suelto → una línea `proveedores.mirar(resp)`
    en el login y en la llamada principal.

⚠️ **Y la guarda, que es lo que hace que no vuelva**: un test escanea el repo
buscando quién menciona `aca.aunesa.com` y **exige** que ese módulo pase por
`core/aunesa` o llame a `mirar`. Un cliente suelto más, mañana, deja de ser un
punto ciego silencioso y pasa a ser un test rojo. (Con su propio test de que el
escaneo sigue encontrando los cuatro — un parametrizado sobre una lista vacía
pasa en verde sin mirar nada.)

#### 2) La correlación no reconocía el nombre

SALUD nombra sus chequeos **`job:<label del crontab>`**, y ese label es libre:
`portafolio_diario` corre `jobs.portafolio_backfill`. El grafo de dependencias
se arma con nombres de MÓDULO, así que `de_quien_depende("job:portafolio_diario")`
devolvía **vacío** — y la pantalla seguía mostrando exactamente lo que §0.af vino
a arreglar:

    proveedor_caido   Aunesa no responde
    salud_job         portafolio_diario: la última corrida falló

La traducción label → módulos ya existía en `jobs_catalogo` (parsea el crontab).
Se **delega**, no se copia. Ahora resuelve también las cadenas: `negocio_chain`
es una línea con varios `-m jobs.x`, y si cualquiera le pega a un proveedor, la
corrida entera depende de ese proveedor.

> Las dos mitades del arreglo tienen la misma forma: **el dato ya estaba y nadie
> lo cruzaba.** El 500 lo vio `requests`, el mapeo lo tenía `jobs_catalogo`. Lo
> que faltaba era que cada uno se lo contara al otro.

#### Y `controles_datos`, que moría todas las noches

Importaba `core/ai_resumen`, borrado el 2026-08-19 con el copiloto. Corría los 20
controles y explotaba con `ModuleNotFoundError` **al final**: calculaba todo y no
persistía ni avisaba nada. No se reemplaza por otra IA — rige §0.k: *una tarea de
IA existe solo si alguien lee su salida*.

### 0.ao «YA LO COMPLETÉ 40 VECES» — el voto no se vuelve a pedir (2026-08-20)

    *«¡Otra vez lo mismo, ya lo completé 40 veces y sigue apareciendo! No puede
    ser que no haya un detector o algo de lo que ya hice.»*

Los BOPREALes marcaban **17/17** y los botones ¿ACERTÓ? seguían ahí, intactos, en
cada rueda. Tenía razón y el error de diseño era mío, escrito con todas las letras
en el docstring del componente:

> *«Se puede votar el mismo caso muchas veces y todas quedan. Si el agente cambia
> de opinión sobre LOC6O dentro de un mes, la historia de los dos juicios es
> justamente lo que dice si mejoró.»*

El razonamiento sirve **solo si cambia la CAUSA**. Repetir el MISMO juicio sobre
el MISMO par no agrega un dato: infla el denominador y, sobre todo, convierte la
pantalla en un formulario que hay que volver a llenar todas las mañanas.

> **El HALLAZGO reaparece cada rueda y está bien** —el problema sigue existiendo—
> **pero el VOTO mide al AGENTE, no al día.** Confundir las dos cosas es lo que
> hacía que un problema abierto pareciera una tarea pendiente.

Ahora el par que se vota es **(caso, causa)** y se pide una sola vez:

  · `votar()` frena el voto humano repetido y devuelve `duplicado` — el freno
    está en el BACKEND, así que aunque la pantalla se equivoque la base no
    acumula 40 filas idénticas;
  · **cambiar de opinión SÍ entra**: si el `acierta` es distinto es una
    corrección, y esa es la información que sí sirve;
  · si el agente cambia de CAUSA para ese bono, es un par nuevo y se pregunta;
  · la vista manda `ya_votado`/`voto` (UNA query para toda la lista) y el front
    muestra «✔ ya votaste: acertó» con un **cambiar** al lado — un voto que no se
    puede corregir se vota mal una vez y queda mal para siempre.

#### Y el aviso del proveedor, que se vio en producción

    AUNESA CAÍDO · HTTP 400 en operaciones/informes
    Fallan 2 de 5: el padrón de cuentas, la tenencia del día y el cost-basis. El
    resto entra bien. Se cae: Tesorería, saldos, tenencia e informes. Lo cargado
    a mano sí está. Fallan 2 de 5: el padrón de cuentas, la tenencia del día y
    el cost-basis. El resto entra bien. Falló hace 6 segundos · último OK

Tres cosas mal en cuatro renglones:

  1. **La misma frase DOS VECES.** `avisar_caida` anteponía el análisis del
     barrido… y el cuerpo ya lo traía adentro. Dos copias de la misma línea en
     un aviso corto es lo que hace que se deje de leer.
  2. **Sin hora.** *«Falló hace 6 segundos»* a los diez minutos ya miente, y no
     se puede cruzar con el job que falló ni con lo que vio la mesa. Ahora va el
     reloj: **cuándo cayó** y **cuándo fue el último OK**. Lo mismo en la vuelta:
     «Volvió 16:51 · cayó 16:38 (13 min caído)».
  3. **Texto del medio que no decide nada** (*«el resto entra bien»*, *«lo
     cargado a mano sí está»*). Fuera. Queda **qué falla y cuándo**:

         Falla: informes (HTTP 400) · padrón de cuentas (HTTP 400)
         Cayó 20/08 16:44 · último OK 20/08 16:38

El «qué se rompe» y el detalle de la prueba siguen en la evidencia para el que
abra el detalle — lo que salió es el renglón, no el dato.

### 0.ap LAS PUERTAS: cuánto de lo que ve, puede resolver (2026-08-20)

**El paso de arquitectura que dictó el caso BOPREAL** (§0.am). Un hallazgo sin
arreglo posible no es un aviso: es una **pared**, y una pared que aparece todas
las ruedas enseña a ignorar la lista entera.

Pero para atacar paredes hay que poder **contarlas**, y no se podía:
`ACCION_POR_TIPO` decía `None` para **12 de 18 tipos**, mezclando cuatro cosas
que no se parecen en nada.

| se veía igual | pero es | ¿deuda? |
|---|---|---|
| `hueco_de_curva` | se acciona por OTRA vía (ME PREGUNTA) | no |
| `recuperado` · `respuesta` | una BUENA NOTICIA: no hay qué arreglar | no |
| `dato_partido` · `permiso_flojo` | se decidió NO automatizar, a propósito | no |
| `motor_caido` · `tabla_quieta` · `cron_desalineado` | se arregla AFUERA y **se podría cerrar** | **SÍ** |

Los cuatro eran la misma fila sin botón. Por eso **la única forma de saber que
`pata_equivocada` era la pared más cara fue que alguien se hartara de verla** —
después de 17 votos.

#### Qué cambia al declarar el motivo

  1. **La pantalla dice por qué no hay botón.** Una fila muda se lee como que el
     agente no sabe qué hacer con lo que él mismo encontró; una que dice «se
     arregla relanzando el job» es información.
  2. **El agente mide su propia cobertura** y ordena la deuda **por volumen** —
     cuánto ruido hace cada pared. Arreglar la que sale 48 veces vale más que la
     que sale una, y eso es un número, no una corazonada.

`SIN_PUERTA` se DECLARA (igual que `DE_QUIEN` y `DOMINIO_EVAL`) y **un test exige
que todo tipo sin acción esté ahí**: un tipo nuevo no puede volverse otra fila
muerta en silencio. Un tipo desconocido cae **del lado de la deuda** a propósito
— asumir «no se puede» escondería el hueco justo cuando nadie lo declaró.

⚠️ **La deuda NO es «todo lo sin puerta»**, y mezclarlos daba una cobertura
falsamente mala: nadie sabría cuál de los dos números mirar. Se agrupa por
**REGLA** y no por tipo, porque la regla es la unidad que se convierte en acción
(fue `pata_equivocada`, no `precio_moneda`).

    python -m scripts.diag_puertas

#### Lo que la medición ya sugiere

Con una mezcla parecida a la de la pantalla de hoy (**simulada, no medida contra
prod**): ~89% con puerta, y **toda la deuda restante es la misma capacidad** —
`sin_escribir`, `machaca` y `sin_producir` piden las tres *relanzar algo*.

> O sea que la próxima puerta no es de bonos: es **una sola capacidad —relanzar
> un job o un motor— que cierra la deuda entera de una**. Eso es exactamente lo
> que esta medición existe para decir, y es lo contrario de lo que uno elegiría
> mirando la pantalla, donde lo que abunda son los bonos.

El número real sale de correr el diag contra la corrida de prod. Y esa puerta
tiene su propia condición, ya escrita en `ACCION_POR_TIPO`: relanzar tiene
efectos afuera de `mercado.curvas`, así que **se habilita cuando el eval set diga
que el diagnóstico acierta** — primero ver, después simular, después escribir.

### 0.aq UNA TABLA DE EVENTOS NO TIENE CADENCIA: TIENE OCASIONES (2026-08-20)

La medición de §0.ap contra prod dio **105 hallazgos · 84 con puerta (80%) · 20 de
deuda**, y la pared #1 fue clarísima:

    1. sin_escribir  ×8   [tabla_quieta]   falta: relanzar el job de esa tabla
       ej: ia.trazas, manager.role_audit, manager.salud_eventos

**Y las tres de ejemplo no tienen ningún job atrás.**

    ia.trazas             ← `core/ai.py`, una fila por CADA llamada al LLM
    manager.role_audit    ← `core/roles.py`, cuando alguien CAMBIA un rol
    manager.salud_eventos ← cuando un chequeo TRANSICIONA

Están quietas **porque no pasó nada**, no porque algo esté roto. Y no hay nada
que relanzar: el job no existe.

> **Casi construyo un botón «relanzar» para esa pared.** Habría sido una puerta a
> ninguna parte — peor que no tener puerta, porque encima promete. La medición
> sirvió para lo contrario de lo que uno espera: no me dijo qué construir, me
> dijo **que la pared más grande no era una pared**.

Es la otra mitad de §0.u. Allá una RÁFAGA se leía como ritmo; acá **un ritmo REAL
se lee como una obligación**: `ia.trazas` escribe casi todos los días porque se
usa IA casi todos los días — hasta el día que no, y ese día no hay nada roto.

#### Cómo se sabe, sin ninguna lista

Igual que `core/dependencias`: **la respuesta ya está en el código**. Quién le
hace el `INSERT` vive en un archivo, y la carpeta dice quién lo dispara.

| escritor | lo dispara | ¿se le exige? |
|---|---|---|
| `jobs/` · `engines/` | un RELOJ (cron, loop de motor) | **sí** |
| `core/` · `api/` | un EVENTO (una request, una acción) | no |

⚠️ **`no sé` NO es `evento`**: si no se encuentra el escritor, la tabla se sigue
exigiendo. Dejar de mirar algo porque no lo entendimos es cómo se pierde una
señal de verdad — sería el bug contrario, y peor. Y `scripts/` no cuenta: un
one-shot corrido a mano no es el escritor habitual de nada.

De yapa el hallazgo ahora trae **`relanzar`**: el módulo exacto, derivado y no
adivinado. Es justo lo que la puerta va a necesitar el día que exista.

> ⚠️ **El bug que casi lo deja midiendo la mitad, y lo cazó su test.** El mapa
> saltaba los archivos con `if "INSERT" not in texto` — y `motor_cedears`, que
> escribe solo por `pg_mirror`, no tiene esa palabra en ninguna parte: quedaba
> afuera **en silencio**. Un filtro de performance que achica lo medido sin
> avisar es el mismo bug que el `for r in app.routes` que veía 5 de 428 (§0.s).
> Con las dos formas: **de 47 a 158 tablas mapeadas**.

### 0.ar REHACER EL DÍA — con la prueba mirada, no con el error (2026-08-20)

El AuM del 2026-08-20 **no se escribió**: Aunesa devolvió HTTP 500 a las 11:00 y
`jobs/portafolio_backfill --diario` murió. El AuM, la Tenencia Valorizada y
Títulos en Alquiler mostraron el día anterior **sin ningún cartel**. Lo vimos de
casualidad, mirando otra cosa.

El user pidió las dos mitades, y la segunda es el diseño entero:

> *«que mismo tenga la skill o que lo pueda hacer (o sea, ejecutar fecha de hoy
> por haber detectado un error **y haber verificado 100% en la base que no hay
> fecha realmente** con lo que iba de hoy)»*

    el job falló     → una señal del PROCESO. Puede fallar y haber escrito.
    el dato no está  → un hecho sobre el RESULTADO. Es lo único que importa.

Un job que revienta al final después de escribir todo no necesita relanzarse;
uno que sale en verde sin escribir una fila, sí. Por eso **la precondición se
consulta contra la tabla y manda sobre el estado del job** — la misma ley que los
CONTRATOS de SALUD: se chequea el resultado, no el proceso.

**Las cuatro guardas** (`api/services/av_agent_rehacer.py`):

1. **Sin evidencia no corre.** Si la fecha ya está, no se ejecuta nada.
2. **Por `run_job.sh`**: lock + timeout, el mismo que usa el cron (REGLA #4). Si
   la corrida anterior sigue viva, esta se saltea sola en vez de apilarse — el
   incidente de CPU del 2026-06-03.
3. **Solo jobs declarados** (`REHACIBLES`), cada uno con su tabla y su columna
   de fecha. Un `subprocess` con el comando abierto sería una consola remota.
4. **Se verifica releyendo la tabla.** Que el proceso salga 0 no prueba nada.

⚠️ **NO relanza motores.** Un motor en rueda le corta el feed de precios a la
mesa (regla del user, 2026-08-18) y eso no se decide desde un botón.

⚠️ **Y EL DÍA QUE LE TOCA NO ES HOY.** `--diario` snapshotea el hábil ANTERIOR.
Exigirle el día de hoy lo daría por faltante **todas las noches**, y un detector
que grita siempre enseña a ignorar la lista entera — la enfermedad que el agente
vino a curar. El día lo declara el job (`"dia": "habil_anterior"`) y lo resuelve
`fecha_objetivo()` **con el mismo reloj que usa el job**: si el que pregunta
calculara su propia fecha, entre las 00 y las 03 UTC diferirían un día y las dos
mitades seguirían siendo coherentes consigo mismas (REGLA #9).

Enchufado: control diario **`dia_sin_dato`** (16:30 UTC, cinco horas y media
después del job) → acción **`sistema.rehacer_dia`**. El control lee la MISMA
lista que el arreglo, así no puede cantar un faltante que la acción no sabe
rehacer. A mano: `python -m scripts.diag_rehacer` (solo mira) y `--rehacer`.

#### Y LO QUE LO HIZO INVISIBLE: cuatro módulos mudos

`jobs/aum`, `jobs/cashflow`, `jobs/sync_comitentes` y `api/services/aunesa_negocio`
le pegan a Aunesa con su propio `requests`, por fuera de `core/aunesa`. El
detector de caídas (§0.ad) mira `manager.proveedor_estado`, que se llena desde
adentro del cliente: **un módulo que no pasa por el cliente es invisible**, aunque
sea el que rompe el dato más importante del sistema.

Migrarlos enteros es otro trabajo y toca cuatro flujos. Lo que cierra la ceguera
hoy con **una línea por módulo** es `proveedores.sesion_vigilada()`: una
`requests.Session` con un hook de respuesta, que se dispara en CADA llamada de
ese archivo — así una función nueva ahí queda cubierta sin que nadie se acuerde.
De yapa reusa la conexión TCP, que en un job de cientos de llamadas no es poco.

> ⚠️⚠️ **Y ESO MISMO CASI INVENTA UNA CAÍDA.** El hook quedó al lado de `mirar()`,
> que ya existía y hacía lo mismo, **con otro umbral**: `mirar` marcaba caída
> desde 400 y el hook desde 500. Con los dos enganchados a la misma llamada, un
> **400 escribía «AUNESA CAÍDO» y enseguida «recuperado»** — una caída inventada,
> prendiéndose y apagándose sola. Y `jobs/cashflow` maneja el 400 de Aunesa
> explícitamente, o sea que no era hipotético. Es REGLA #9(B) en vivo: dos copias
> del mismo criterio, sin árbitro, cada una coherente consigo misma.
>
> **RESUELTO**: el umbral vive UNA vez en `proveedores.es_caida()` (solo 5xx — un
> 4xx es problema NUESTRO, y el 401 es el token vencido que los jobs resuelven
> re-autenticando), `rastrear` pasó a ser `vigilar`, y `vigilar` es idempotente
> (`jobs/aum` tenía los dos hooks sobre la MISMA sesión). Tres tests lo congelan,
> incluido uno que exige que los dos caminos deriven del mismo `es_caida`.


### 0.as EL BOTÓN QUE NO ARREGLABA NADA — por qué volvían 17 veces (2026-08-21)

El user, con los BOPREALes en **17/17 votos** en la pantalla:

> *«¡otra vez lo mismo, ya lo completé 40 veces y sigue apareciendo! No puede ser
> que no haya un detector o algo de lo que ya hice.»*

**No era el detector. Era el botón.** `precio_moneda` agrupa dos problemas que se
arreglan distinto, y la acción de la fila salía del TIPO:

    cotiza_en_pesos   → NO hay pata en dólares → hay que ir a BUSCARLA   (pata)
    pata_equivocada   → la pata existe y cotiza; el MASTER apunta mal    (apuntar)

Los dos mostraban **«BUSCAR LA PATA USD»**. Para el segundo eso pide una pata que
ya cotizaba y **deja `mercado.curvas` apuntando a la de pesos**: el user apretaba,
salía «✔ pedida», y a la rueda siguiente estaban los 17 de nuevo. La acción que sí
lo arregla (`mercado.apuntar_pata`, §0.am) existía desde el mismo día, pero solo se
llegaba por la tab de propuestas — tres pantallas más allá del problema.

> Un botón que no arregla el problema de esa fila es PEOR que no tenerlo:
> promete, no cumple, y no da un solo error.

**El arreglo tiene dos mitades.** (1) `av_agent.ACCION_POR_REGLA` + `accion_de()`:
**la REGLA gana sobre el TIPO** — el tipo es la familia del problema, la regla es
la causa, y la causa es lo que decide el arreglo. Se declara en el backend, no con
un `if` en el front, porque una segunda tabla de acciones del lado de la pantalla
se separa de esta sin dar ningún error. (2) `av_agent_hacer.uno(accion, sujeto)`:
una acción sobre UN sujeto desde la fila — arma el caso volviendo a correr el
control, propone con la MISMA acción y aplica por la MISMA `aplicar()`, así el
libro, la verificación y el «esperando respuesta del mercado» funcionan igual que
por el otro camino. Endpoint `POST /av-agent/pata/apuntar`, con `aplicar=false`
por default (sin eso **no escribe**: devuelve qué haría).

### 0.at ENCONTRÓ MUESTRA LO QUE FALTA, NO TODO (2026-08-21)

> *«¿Podemos que ENCONTRÓ muestre por defecto lo que NO hice? Que estos queden en
> ENCONTRÓ pero marcados como ya hechos.»*

Con 107 filas de las que la mayoría ya habían pasado por sus manos, la lista de
trabajo dejó de ser una lista de trabajo: para saber qué faltaba había que ir
leyendo cuál tenía el ✔ y cuál no, fila por fila.

**ATENDIDO = ya pasó por tus manos**: aplicaste su arreglo (`aplicado`) o lo
votaste (`votado`). La marca dice cuál de las dos fue.

> ⚠️ **La primera versión de esta regla estaba al revés y hubo que corregirla al
> día siguiente.** No contaba el voto cuando la fila tenía botón, con este
> argumento: *votar no arregla nada, así que esconder un bono votado y roto sería
> peor que el problema*. Eso es correcto sobre el DATO y equivocado sobre la
> PANTALLA — y el user tuvo que pedirlo dos veces: *«que ENCONTRÓ muestre por
> defecto lo que NO hice… si no es imposible avanzar»*.
>
> Si votó, lo miró. Que además falte apretar el arreglo se dice **con la marca**,
> no dejando la fila arriba de todo como si nunca la hubiera visto. Y lo que
> impide esconder algo roto no es este filtro: la fila sigue en la lista, contada
> arriba y a un clic. Esconder con el número a la vista no es truncar; dejar 107
> filas donde 90 ya se miraron **sí** es perder la lista de trabajo.

Se deriva en la lectura (`av_agent_vista`), como `accion` y `de_quien`, así el día
que un tipo consiga su puerta los hallazgos ya guardados se re-evalúan solos. Y
**no saca la fila de la lista**: la marca y la apaga. El filtro esconde con el
número a la vista (`{n} ya hechos`) y un clic lo destapa — misma regla que el
corte del mercado, *esconder sin decir cuánto es truncar en silencio*.

Si no se puede leer lo aplicado, **todo queda pendiente**: una fila de sobra
molesta, una fila escondida que estaba rota no se ve nunca.

### 0.au MENOS TEXTO: la misma frase tres veces (2026-08-21)

> *«Necesito respuestas más claras cuando encuentra algo, menos texto y más claro
> cuál es el problema.»*

Un caso del informe salía así — tres títulos distintos, **una sola frase**:

    · La escala del cuadro que YA está cargado:  Σ = 149.250,00 … 1.492× más grande
    · Dónde está el problema, por división:      paridad = 137.280 / 149.250
    · El valor técnico: ¿en qué escala está…?:   Σ = 149.250,00 … 1.492× más grande

`_sin_repetir` ya existía y **solo corría para el LLM**: el comentario decía que
«para una persona esa redundancia ayuda, cada lente se lee sola». No ayuda — hace
dudar de si son tres problemas o uno. Ahora la deduplicación va **en el origen**
(`_diagnosticar_uno`), topeada en 3 trabas, así la pantalla y el texto para copiar
no pueden mostrar cosas distintas.


### 0.av EL CÍRCULO SE CIERRA: ¿el arreglo FUNCIONÓ? (2026-08-21)

El paso siguiente del eval set no era construir nada nuevo: **era enchufar lo que
ya estaba.** `av_agent_seguimiento` (§0.ac) contesta desde el 2026-08-19 la
pregunta que el user pidió —*«que el agente entienda cuándo hizo algo bien, no
porque yo le puse "acertó", sino porque a los días detecta que el cambio tuvo
consistencia»*— y **nunca recibió un caso de las 8 acciones**.

Se alimenta desde `av_agent_acciones.registrar`, y `av_agent_hacer._aplicar_una`
—el camino que usan TODAS las acciones, incluidos los botones de fila— no lo
llamaba nunca. **Medido: cero apariciones de `registrar` en ese archivo.**

    Verificar que la escritura ENTRÓ no dice si el arreglo era el CORRECTO:
    un símbolo mal puesto se escribe igual de bien que uno bien puesto.

Las dos mitades estaban en el mismo repo sin tocarse. Nada fallaba: el libro no
registraba estas acciones (así que tampoco salían en la tab HIZO, contra la regla
de auditoría de `CLAUDE.md`) y el seguimiento se quedaba vacío.

**Enchufado**, con tres cuidados: va DESPUÉS de sellar y en su propio `try` (la
escritura real no se deshace por un problema de medición); **solo si quedó**
—poner en seguimiento algo que falló mediría un arreglo que no existe y a los 5
días lo cantaría como «volvió», culpando al diagnóstico de un error de
escritura—; y el `destino` del libro se **deriva del registro** (`campo`/`donde`)
en vez de copiar los 8 ids a mano, que es cómo se consigue un libro que dice «?».

#### ⚠️ LA CAUSA NO ES EL CONTROL, y ese desalineo trababa la compuerta

El control se llama **`patas_equivocadas`** (plural) y el detector emite
**`pata_equivocada`** (singular). Son los MISMOS bonos. Con la clave del control,
los 17 votos humanos de los BOPREALes y los votos derivados de sus arreglos se
habrían contado **por separado**, y ninguna de las dos mitades habría llegado
nunca a `MIN_VOTOS`. REGLA #9 aplicada a la única compuerta que habilita
autonomía: dos copias del mismo criterio, sin árbitro, cada una coherente consigo
misma. Por eso `Accion.causa` se declara aparte de `sobre`, con un test que exige
que sea una regla que un detector realmente emite.

### 0.aw UNA CAUSA PROBADA DEJA DE PREGUNTAR (2026-08-21)

`ya_votado` (§0.ao) corta la repetición por **CASO** —el mismo bono con la misma
causa— y no alcanza: con `pata_equivocada` en **17/17**, un BOPREAL nuevo seguía
pidiendo el voto 18. Lo que se mide es si el agente entiende esa **CAUSA**, y eso
ya está contestado; otro voto no agrega evidencia y es exactamente la fatiga que
el user viene marcando desde hace tres días.

`confianza.probada` usa **el mismo umbral que `candidata_a_auto`**, leído de la
misma cuenta: si la pantalla usara otro criterio, diría «probada» sobre algo que
el tablero todavía llama «sin evidencia». **No es irreversible**: se puede votar
igual desde CAMBIAR, y un ✖ la baja del umbral sola en la próxima lectura — que
es justo la señal de que el agente empeoró.

> Y la evidencia más fuerte no la pone nadie: el `verificado` que sale del
> seguimiento **cuenta como un voto humano** para la compuerta, porque el
> problema volvió o no volvió y el agente no controla eso. El `derivado` (una
> aprobación) queda afuera: eso es alguien diciendo «dale», no el mundo diciendo
> «funcionó».


### 0.ax ¿ACERTÓ QUÉ? — no todo hallazgo es un juicio (2026-08-21)

El user, mirando tres filas de `motor_ruidoso`:

> *«Es inentendible si acertó o no. O sea, ¿acertó QUÉ? Si ni se entiende cuál
> fue el error. Algunos son siempre SÍ claramente, si detecta solo que hay algo
> que está pasando. Pero **¿qué hacemos con eso?**»*

Hay dos cosas distintas mezcladas en la misma lista, y la pantalla las trataba
igual:

    JUICIO       el agente DEDUJO una causa y puede errarle. «el master apunta
                 a la pata equivocada». Ahí ¿ACERTÓ? es LA pregunta.

    OBSERVACIÓN  el agente COPIÓ un hecho. «el motor escribió esta línea de
                 ERROR», «el proveedor devolvió 500». No hay nada que acertar:
                 preguntarlo es preguntar si el log existe.

**Y el daño no era solo la confusión.** Esas filas se votan SIEMPRE que sí,
llegan a 10/10, la causa se marca `candidata_a_auto` y el tablero afirma que el
agente es infalible en algo donde **nunca emitió un juicio**. La compuerta de la
autonomía se habría abierto con evidencia que no mide nada.

A una observación se le pregunta lo único contestable y que además sirve:
**¿te sirve verla?** — un «no» es «dejá de mostrármela», que es exactamente el
*«¿qué hacemos con eso?»*. Vota con `origen='utilidad'`, y como
`precision_por_causa` y `resumen` ya contaban `origen IN ('humano','verificado')`,
queda afuera de la compuerta **sin tocar una sola query**.

Dos decisiones que lo sostienen: se declara por TIPO en `av_agent.PREGUNTA_POR_TIPO`
con un test que exige que ningún tipo caiga en el default (**ante la duda,
JUICIO**: pedir un voto de más molesta, dar por observación una deducción deja al
agente sin medición donde puede errarle); y **el `origen` NO viaja en el body** —
lo decide el backend leyendo el tipo, porque si el front pudiera mandarlo, una
pantalla vieja o un `curl` moverían la compuerta.

### 0.ay EL PRÓLOGO DEL LOG SE COMÍA EL MENSAJE (2026-08-21)

    motor_curvas: <fecha>,<n> ERROR pg_mirror pg_mirror market_snapshot: de…

Los primeros **38 caracteres son andamiaje**: la fecha (que ya se muestra como
hora al final), el nivel (que ya se muestra como «· error») y el nombre del
logger **repetido**, que es cómo lo formatea `logging`. Lo único que interesaba
—qué le pasó a `market_snapshot`— quedaba cortado por el «…».

`logs_sistema.sin_prologo()` lo saca. **No cambia el agrupamiento** (el prólogo
es idéntico en todas las líneas de la misma unidad) y le devuelve ~40 caracteres
al mensaje real. Ahora:

    motor_curvas: pg_mirror market_snapshot: deadlock al escribir <n> filas · error · 16:49

⚠️ El test `test_la_FECHA_y_la_HORA_no_hacen_dos_problemas` **exigía** que la
fecha quedara en el patrón. Congelaba la implementación, no la intención: lo que
tenía que garantizar es que dos horas distintas sean UN problema, y eso no
cambió.

### 0.az «NUEVO» NO SE LE DICE A ALGO DE HACE 10 HORAS (2026-08-21)

> *«No termino de entender por qué muestra esto ahora.»*

Un `control:patas_dolar_sin_pedir` bajo el título **NUEVO, SIN VER**, con
«desde hace 10 h · ×474» al lado. Y no pasó nada ahora: lo único «nuevo» era que
nadie había apretado el botón de visto.

**Sin ver y recién aparecido son dos cosas distintas**, y llamarlas igual quema
el rótulo: si lo que dice NUEVO tiene medio día, ninguno de los otros carteles se
lee en serio tampoco. Ahora el centinela publica `recien` (`RECIEN_S` = 2 h) y la
pantalla abre dos grupos: **NUEVO, SIN VER** y **VIENE DE ANTES, SIN VER**, cada
uno con su botón. Se calcula en el backend porque el navegador no puede mirar el
reloj mientras dibuja — y porque el criterio tiene que ser uno solo.


### 0.ba EL ERROR, EN CASTELLANO — qué pasó, a qué afecta, si sigue (2026-08-21)

> *«Es imposible entender qué es el error, qué está pasando o qué pasó, si sigue
> pasando. Está el mensaje cortado, aparte todo súper técnico, no se entiende a
> qué está afectando de la app. **Poner una línea de código y decir que no anda
> es inentendible.**»*

Lo que se veía:

    motor_curvas: <fecha>,<n> ERROR pg_mirror pg_mirror market_snapshot: de…
    controles_datos: la corrida de 20/08 16:30 UTC falló          [ANALIZAR]

Y la frase que define el diseño:

> *«Los motores y los jobs hacen cosas **LINEALES**, no son a interpretación.
> Siempre tienen que estar analizados.»*

De un error de NUESTRO sistema hay que poder contestar tres cosas **sin apretar
nada**: **QUÉ PASÓ** (en castellano) · **A QUÉ AFECTA** (qué pantalla queda mal)
· **SI SIGUE** (terminó, o está pasando ahora).

    motor_curvas: no pudo guardar en la base lo que calculó · error ×3 · 16:49
      No pudo guardar en la base lo que calculó. El dato se calculó bien y se
      perdió en la escritura.
      AFECTA: la TEA, la paridad y la duration de cada bono → la tabla de RENTA FIJA.
      Log: 2026-08-21 16:49 ERROR pg_mirror deadlock detected

**LA REGLA PRIMERO, LA IA DESPUÉS.** `av_agent_errores.FIRMAS` traduce lo
conocido gratis y sin poder alucinar — son piezas nuestras, fallan de un
conjunto finito de formas, y un `ModuleNotFoundError` significa siempre lo
mismo. El modelo entra **solo donde la regla no supo** (pedido del user: *«acá
es donde hay que meter un LLM que explique qué es el error»*), con tres guardas:

  1. **Se explica el PATRÓN, no la fila.** El mismo error sale 90 veces en 6 h;
     una llamada por aparición sería absurdo. Una explicación por
     `(unidad, patrón)`, persistida en `mercado.av_agent_errores` — misma idea
     que agrupar el log. Por eso la tarea es `flash`/300 tokens y no `pro`.
  2. **La IA NO decide a qué afecta.** Eso sale de la ficha declarada
     (`salud.JOBS[…]['alimenta']`, `QUE_HACE`), que es un hecho del sistema.
     Inventar consecuencias es lo que haría desconfiar de todo lo demás.
  3. **Sin IA se muestra la línea cruda**, marcada `fuente='crudo'`: un texto
     feo es mejor que ninguno, y ese contador es la lista de lo que falta cubrir.

Tres cosas más que salieron de esto:

- **La severidad la decide la CONSECUENCIA, no el nivel del log.** Un WARNING
  repetido 90 veces por contratos vencidos disparaba `machaca` en severidad
  ALTA; un ERROR de escritura es plata que no se guardó. Ahora las firmas que no
  son urgentes bajan la fila a media.
- **`salud` pasó a OBSERVACIÓN.** Que una corrida falló lo dice `job_runs`:
  preguntar «¿el agente acertó?» no tiene respuesta posible — acertó por
  construcción (§0.ax).
- **El `pasa` de cada firma es CORTO a propósito** y hay un test que falla si el
  título se corta: el título tiene 88 caracteres contando el motor, la cuenta y
  la hora, así que una frase de 110 vuelve al «…» que originó todo esto. El
  matiz va al cuerpo, donde sí hay lugar.

⚠️ **Los hallazgos se PERSISTEN**, así que las filas viejas siguen mostrando el
texto con que se detectaron: el título nuevo aparece cuando el detector vuelve a
correr (el monitor, cada 5 min en rueda; los de SALUD se evalúan en vivo).


### 0.bb TRES BUGS QUE SE VEÍAN COMO «EL AGENTE NO TIENE MEMORIA» (2026-08-21)

> *«No entiendo cómo estás solucionando las cosas. El 50% de esta conversación
> debe ser por los mismos temas y sigue pasando como si nada.»*

Tres cosas distintas que **se veían igual**: que el agente no se entera de nada.
Ninguna era de memoria; las tres eran bugs concretos y medibles.

#### 1. Clasificaba UNO de 19 · el `[` que rompía todos los anclajes

Sobre 19 assets sin cartera proponía **una** sugerencia. La tabla de patrones
estaba bien: el problema es que las unidades de Aunesa vienen en DOS formas de
corchete y `_nombre()` solo sacaba una.

    [42932] OTC SOJ.        el corchete es un PREFIJO (el id de especie)   ✔ se sacaba
    [OTC - MAI.ROS/ENE27]   el corchete ENVUELVE al nombre entero          ✖ quedaba

Los patrones de contrato de cámara están anclados con `^` —a propósito, para que
un «GIR.» adentro de una razón social no convierta un bono en derivado— y **con
el `[` adelante ninguno podía matchear**. El único que salía, `[OTC - DLR052027]`,
salía **de casualidad**: pegaba con `\bDLR\s*\d`, el único patrón sin ancla.

Medido: **de 1 a 9** contratos de cámara clasificados, con un test que congela la
guarda del `^` (un «GIRO» en el medio de una razón social sigue sin ser derivado).

#### 2. El re-chequeo decía «resuelto» y abajo listaba lo viejo

    ↻ CHEQUEAR AHORA  →  «0 siguen · 4 se resolvieron»
    ...y abajo seguían los 4 casos, con «hace 7 d»

**El agente SÍ tenía memoria**: `_diff_y_persistir` había cerrado los 4 en
`manager.controles_datos`. Lo que faltaba era que `recontrolar` **devolviera el
diagnóstico recalculado** — pintaba el conteo nuevo arriba de la cadena vieja.

> Dos verdades contradiciéndose en la misma tarjeta se leen como que el sistema
> no se enteró. Es peor que no haber puesto el botón.

Ahora devuelve `diagnostico` + `resuelto`, la pantalla pisa la cadena y canta
«ya no queda ninguno».

#### 3. Daba de alta contrapartes SIN CLASIFICAR

> *«No toma en cuenta todos los casilleros de clasificar una contraparte, no
> pidió si es fondo, ALYC o qué. Le falta contexto.»*

`BCO CREDICOOP TERCEROS` vino con `segmento=None` y **se dio de alta igual**. El
libro decía «contraparte = Credicoop» y `verificar` daba OK porque solo miraba el
nombre. Una contraparte sin segmento no está dada de alta: está a medias, no
rompe nada hoy, y cuenta mal en todo lo que agrupe por segmento sin que nadie se
entere — REGLA #9 otra vez.

Ahora el valor propuesto es **`Nombre · Segmento`** (un campo editable, los dos
corregibles), sin segmento **no escribe**, `verificar` exige los dos, y el
`codigo_mae` —que no se puede adivinar, lo asigna el MAE— se **declara
pendiente** en vez de quedar en silencio.


### 0.bc UN SOLO VOCABULARIO PARA EL CICLO DE VIDA (2026-08-21)

> *«Los avisos, lo que encuentra, los mensajes… deberían estar codeados como
> **objetos con sus estados**. Porque si no, esto va a escalar mal y siempre se
> va a solucionar sobre la marcha.»*

Tenía razón, y el número lo dice: **22 tablas del agente, 8 formas distintas de
decir las mismas tres cosas** (está abierto · lo vi · se resolvió).

    resuelto_at IS NULL     controles_datos · av_agent_avisos · av_agent_centinela
    resuelto boolean        av_agent_avisos            ← ¡las DOS en la misma tabla!
    estado text             av_agent_preguntas · av_agent_runs · av_agent_propuestas
    hecho boolean           av_agent_aviso_items
    visto_at                salud_vistos · av_agent_centinela
    ok boolean              av_agent_acciones · manager.proveedor_estado
    aplicada_at             av_agent_preguntas · av_agent_propuestas
    la EXISTENCIA de la fila   av_agent_ignorados y 11 más

⚠️ **Y la más importante no tiene estado.** `mercado.av_agent_hallazgos` —la que
llena ENCONTRÓ— es una **FOTO** con `corrida_at`. Todo su ciclo (atendido ·
visto · ignorado · vencido · ya votado) se **deriva en la lectura**, cruzando
otras cinco tablas, en funciones distintas.

**De ahí salieron los bugs de esta semana, y son todos el mismo bug**: dos
pantallas derivando el mismo estado con criterios distintos. `atendido`,
`recien`, `ya_votado`, `sin_puerta`, `resuelto` — cinco derivaciones escritas en
cinco lugares en cinco días. Ninguna falla sola; se contradicen entre ellas, que
es el modo de falla de REGLA #9 y por eso siempre se descubren mirando la
pantalla.

#### Lo que se hizo, y lo que NO

`core/ciclo.py` **no migra ninguna tabla** — migrar 22 de un saque es cómo se
rompe un sistema que funciona. Hace lo que ya funcionó tres veces acá
(`api/superficie.py`, `core/duplicados`, `core/escribe`):

1. **Declara el ciclo UNA vez**: `nuevo · visto · en_curso · resuelto ·
   ignorado · volvio`, con sus transiciones válidas. Seis, y cada uno existe
   porque **se atiende distinto** — un estado de más es una rama de más en cada
   pantalla, para siempre.
2. **Declara cómo lo dice hoy cada tabla**, con su traducción. Se declara y no
   se adivina: `resuelto_at IS NULL` y `resuelto = false` parecen lo mismo y
   `av_agent_avisos` **tiene las dos** (gana el booleano, que es el que filtran
   sus queries).
3. **Un test que FALLA** cuando aparece una tabla nueva sin declarar. Es la
   guarda que impide que las 8 formas se hagan 9.

`estado_de(tabla, fila)` es el árbitro: la pantalla pregunta ahí en vez de mirar
la columna, así dos pantallas no pueden discrepar. Una tabla desconocida cae en
`nuevo` —el estado más ruidoso, a propósito: ante la duda se muestra de más.

**`VOLVIO` es el estado que más importa.** Un problema que reaparece **no es
nuevo**, y contarlo como nuevo es exactamente cómo se pierde que algo se arregla
y se rompe todas las semanas — lo que pasó con los BOPREALes durante 17 ruedas.

> ⚠️ **Esto frena la sangría; no cura la herida.** Las derivaciones que ya
> existen siguen donde están hasta que cada superficie se mueva acá. La
> migración va tabla por tabla y **la deuda es un número**: `sin_migrar()` →
> **11**, y `python -m scripts.diag_ciclo` la lista con qué dice cada una hoy.
> Que se pueda contar es la mitad del valor: sin eso, «hay que unificar los
> estados» es una intención y no una tarea.


### 0.bd EL OBJETO: uno solo, y el tipo es un campo (2026-08-21)

> *«Que todo lo del AV Agent esté como objeto. Va a ser **siempre el mismo
> estilo**, solo que va a cambiar el TIPO de problema —log, aviso, etc.— pero
> **cómo van a estar es lo mismo**. Después cambiará la solución, el análisis.»*

Es la descomposición correcta, y nombra tres cosas que varían por separado y
estaban mezcladas en 22 tablas:

    LA FORMA      cómo se guarda y cómo vive        → UNA
    EL TIPO       de qué habla (bono · job · log)   → un CAMPO
    LA SOLUCIÓN   qué se hace y cómo se explica     → enchufable, por tipo

`mercado.av_agent_items` + `core.ciclo.Item`. Un bono mal cargado, un job caído,
una línea de ERROR de un motor, un aviso a una persona y una pregunta abierta
**son la misma cosa** para el ciclo de vida: aparecen, se ven, se actúan, se
resuelven, y a veces vuelven.

#### `clave` ES LA MEMORIA, y es lo que faltaba

La PK es `tipo|origen|sujeto|regla` y **no lleva fecha**. Eso es todo el
arreglo: `av_agent_hallazgos` guarda una FOTO por corrida, así que el mismo
problema se reescribía entero cada vez, sin identidad. Por eso aparecía «nuevo»
todas las ruedas, por eso perdía que ya lo habías votado, y por eso el agente
parecía no acordarse de nada.

Con clave estable, ver el mismo problema mañana **no crea una fila**: actualiza
la que hay. Tres cosas que antes no existían:

  · **`abierto_at` no se pisa nunca** → «apareció hoy» pasa a ser «lleva 11
    días». Sin eso, un problema de hace dos semanas se ve igual de urgente que
    uno de recién y nada acumula antigüedad.
  · **`veces`** cuenta cuántas ruedas lleva sin resolverse.
  · si estaba RESUELTO y reaparece → **`volvio`**, que no es lo mismo que nuevo.

El `titulo` **sí** se refresca: el problema es el mismo pero su explicación puede
mejorar (una firma nueva, una traducción del modelo). Congelar el primer texto
sería quedarse con el peor. Y el estado se decide **en el `ON CONFLICT`**, no
leyendo primero: dos detectores corriendo a la vez no se pueden pisar.

#### El SEGUIMIENTO son HITOS, no un plazo

> *«5 días es mucho. Es el día siguiente para ver si vuelve. Pero a su vez tiene
> que tener memoria y recursos para que siga con el paso del tiempo: puede ser 2
> días, 3 días…»*

Son dos necesidades distintas que un plazo único no cubre, y tenía razón:

    la señal RÁPIDA        si vuelve mañana, el arreglo no sirvió → hito a 1 DÍA
    la CONFIANZA que suma  aguantar un día ≠ aguantar un mes → 1·2·3·7·14·30

Cada hito que pasa sin volver suma confianza (`Item.confianza_del_arreglo`, de 0
a 1). **Volver una vez borra todo lo acumulado**: un arreglo que falla al día 8
no es «7 días bueno», es un arreglo que falla — y si la confianza sobreviviera a
la vuelta, el número mentiría justo en el caso que importa.

#### Convive con lo viejo, y la migración se puede contar

No se migró ninguna tabla: las 22 siguen ahí. Ésta es **el destino**, y el
registro de §0.bc la marca con ⭐ como la única que ya habla el vocabulario sin
traducción. `python -m scripts.diag_ciclo` muestra la barra:

    ⭐ canónica            1
    ✖ deuda (a migrar)    11
    MIGRACIÓN             █░░░░░░░░░░░  1/12

Migrar 22 de un saque es cómo se rompe un sistema que funciona. **El primer
candidato es `av_agent_hallazgos`** — darle identidad al hallazgo en vez de
derivar su estado desde cinco tablas se lleva la mitad de los bugs de esta
semana.


### 0.be LA PRIMERA MIGRACIÓN: el hallazgo tiene memoria (2026-08-21)

`av_agent_hallazgos` era el candidato #1 y se hizo. Ahora cada hallazgo se
espeja como objeto (§0.bd) y **la pantalla lee su historia**.

#### Lo que faltaba no era guardar: era CERRAR

`ver()` sabe que algo sigue. Lo que nadie sabía es que algo **dejó de estar** —
una foto por corrida no puede decir «esto ya no aparece», simplemente sale una
lista más corta. Por eso el agente nunca pudo afirmar que algo se arregló, y por
eso el seguimiento no tenía de dónde arrancar.

`sincronizar(origen, vistos, evaluados)` hace las dos mitades:

    lo que está      → nace, o suma `veces`, o pasa a `volvio`
    lo que YA NO     → `resuelto`, y ahí arranca el conteo de hitos

> ⚠️⚠️ **`evaluados` ES LA GUARDA MÁS IMPORTANTE DE TODA LA MIGRACIÓN.** Una
> corrida puede mirar MENOS de lo que mira siempre: si 1816 no contesta,
> `falta_en_base` no se evaluó — y su lista vacía **no significa que no falte
> ningún bono**, significa que no se miró.
>
> Cerrar por ausencia sin saber qué se miró convertiría **cada caída de un
> proveedor en «se arreglaron 40 problemas»**: el tablero en verde exactamente
> el día que está más ciego. Es la mentira más cara que puede decir una
> herramienta de integridad. El job ya distinguía los dos casos al imprimir
> («los FALTANTES no se evaluaron en esta corrida»); lo que faltaba era que la
> persistencia también los distinguiera. **Sin `evaluados` no se cierra nada.**

#### La identidad se calcula UNA vez

`av_agent_hallazgos` ganó la columna `clave`, que **escribe el detector**. La
pantalla no la recalcula: hace `LEFT JOIN` por esa columna. Si el que lee la
recalculara —en Python o en SQL— habría dos implementaciones de la misma
identidad, y la memoria terminaría existiendo pero inalcanzable. Es el mismo
modo de falla que el símbolo columna-vs-blob (REGLA #9).

⚠️ Y entra **en la misma query**: hay un test que cuenta los viajes de
`_hallazgos_ultima_corrida` porque el peaje de Supabase se paga por viaje
(~8,5 ms). Ese test cazó el intento de resolverlo con una query aparte — y tenía
razón, porque la solución con JOIN además es la correcta.

#### Lo que se ve en la fila

    AL30   pata equivocada   ↩ volvió   11d ×47

`↩ volvió` gana sobre todo lo demás: un problema que se arregló y reapareció
dice que **el arreglo no sirvió**, y es la señal más fuerte que tiene el
sistema. La antigüedad se muestra recién a partir del día — «hace 4 h» no cambia
ninguna decisión y ocupa lugar.

#### La antigüedad no se inventó: estaba en la base

Sellar todo con `abierto_at = ahora` habría sido mentir justo en lo que la
migración vino a arreglar. Pero la tabla conserva **60 corridas**: la primera en
que aparece cada clave ES desde cuándo está abierta, y en cuántas apareció ES el
`veces`. `python -m scripts.backfill_items` lo recupera (dry-run por default,
idempotente, y **no cierra nada** — una clave ausente del último censo pudo
arreglarse o pudo no evaluarse, y desde un backfill no hay forma de saberlo).

> ⚠️ **Y el primer dry-run devolvió `0`, que era un bug MÍO del mismo tipo que
> vengo persiguiendo.** El preview filtraba por `clave IS NOT NULL` y esa
> columna recién se llena en el paso que solo corre con `--aplicar`: el
> resultado decía «0 hallazgos a migrar» cuando la verdad era **«no pude
> mirar»**. Un preview que no puede previsualizar sin escribir primero no es un
> preview. Ahora calcula la clave al vuelo.
>
> Y como eso obliga a tener la identidad en DOS lenguajes (SQL para leer el
> histórico, Python para el resto del sistema), el script **compara las dos
> fila por fila y aborta si una sola no coincide**: escribir items con una clave
> que nadie va a buscar dejaría la memoria escrita y para siempre inalcanzable,
> sin un solo error. REGLA #9 en su forma más cara.

    MIGRACIÓN   ██░░░░░░░░░░  2/12


### 0.bf EL CENTINELA — y un «se arregló solo» que era mentira (2026-08-21)

Segunda migración. Al enchufar el monitor de rueda al modelo de objetos apareció
un bug que llevaba meses ahí y **nadie podía ver**.

`_observar()` mira tres cosas —precios, tasas y salud— **cada una en su propio
`try`**, para que la caída de una no deje al centinela sin mirar las otras. Eso
está bien. El problema era lo que venía después:

    if hallazgos:      # ← «si algo trajo, cerrá todo lo demás»
        UPDATE ... SET resuelto_como = 'solo' WHERE ultimo_at < marca

El comentario decía *«solo cuando la pasada fue COMPLETA»* y **eso no era lo que
el código chequeaba**. Los tres `try` se tragan la excepción y devuelven una
lista más corta, así que el que llama no puede distinguir «no encontró nada» de
«explotó». Con el bloque de tasas caído, los precios igual traían algo, la
condición pasaba, y **todos los `tasa_sospechosa` quedaban marcados como
"se arregló solo"** — en silencio y del lado optimista, que es la peor
combinación posible en una herramienta de integridad.

Es **el mismo modo de falla** que `evaluados` tapó en el censo (§0.be), abierto
en otro lado. Ahora `_observar` devuelve también QUÉ ALCANZÓ A MIRAR, declarado
por bloque en `_CUBRE` — se declara y no se deduce de lo que trajo, porque *una
pasada que no encontró nada y una que explotó devuelven lo mismo: nada*.

#### Y las dos pantallas dejan de contar historias distintas

El centinela tenía su `veces` y su `abierto_at`; la relevada nocturna tenía los
suyos; nadie los unía. Con la misma `clave` es **un solo objeto**: la antigüedad
que ves en ENCONTRÓ es la misma que ve AHORA. Su tabla sigue dibujando la
pantalla; migrarla del todo es el paso siguiente.

> ⚠️ **Y otra vez un test se cazó a sí mismo con su propio comentario** (el
> tercero de la semana): el comentario nombra `if hallazgos:` justo para decir
> que ya no está, y el `assert` lo encontró ahí. Los tests que leen el fuente
> ahora usan un helper que **saca los comentarios**: un test tiene que leer lo
> que se EJECUTA.

    MIGRACIÓN   ███░░░░░░░░░  3/12


### 0.bg LOS CONTROLES, Y EL ESLABÓN QUE FALTABA (2026-08-21)

Tercera y cuarta migración. Las dos cierran el mismo agujero desde puntas
distintas — el que el user viene marcando hace días:

> *«Ya lo marqué como hecho y sigue figurando. No tiene memoria de los cambios.»*

    el control corre     → sabía qué había y qué se había resuelto…
    apretás el arreglo   → se escribía, se verificaba…
    ...y el hallazgo seguía exactamente igual en la pantalla.

**Las dos mitades funcionaban y no se hablaban.** Nada conectaba la ACCIÓN con
el OBJETO.

#### Los controles: acá SÍ se puede cerrar por ausencia

`_diff_y_persistir` ya calculaba bien `nuevos` y `resueltos`; solo faltaba que
eso viviera en el objeto. Y conviene dejar escrito **por qué acá el cierre es el
caso limpio**: a esa función *solo se llega si el control no levantó* (en
`main()` está adentro del `try`; si explota se anota en `errores` y no se
persiste nada). Una lista vacía significa de verdad «no hay anomalías» y no «no
pude mirar» — la distinción que costó los dos bugs anteriores (§0.be, §0.bf), y
que acá está resuelta por construcción. Hay un test que verifica esa premisa en
el código en vez de asumirla.

Un `origen` por control (`control:assets_sin_cartera`): si compartieran uno,
correr un control cerraría las anomalías del de al lado por no haberlas visto. Y
como `_diff_y_persistir` es el único camino de escritura, **el cron y el botón
↻ CHEQUEAR AHORA dejan exactamente el mismo estado.**

#### Aplicar un arreglo mueve el objeto — a `en_curso`, no a `resuelto`

⚠️ **La distinción que hace honesto al sistema.** Escribir el dato no es lo
mismo que el problema haya desaparecido:

    la ACCIÓN dice     «escribí lo que había que escribir»      → en_curso
    el DETECTOR dice   «volví a mirar y ya no está»             → resuelto

Y recién ahí arrancan los hitos. Si la acción se cerrara sola, **el agente
estaría calificando su propio trabajo** — que es exactamente lo que el eval set
existe para evitar.

La `clave` se arma con la misma fórmula desde los dos lados. Si se calculara
distinto, la acción movería un objeto que no existe y el hallazgo seguiría igual
**sin dar ningún error**: el síntoma exacto que esto vino a arreglar (REGLA #9).

    MIGRACIÓN   █████░░░░░░░  5/12


### 0.bh UN PROBLEMA, DOS OBJETOS — y la primera derivación que se va (2026-08-21)

#### El puente que hubo que construir antes de seguir

Al enchufar las acciones al modelo apareció esto, medido antes de tocar nada:

    detector →  precio_moneda|live|bpoa7|pata_equivocada
    control  →  control|control:patas_equivocadas|bpoa7|patas_equivocadas

**Un bono con la pata mal cargada, DOS objetos**, porque lo miran dos cosas
distintas: el detector de rueda y el control nocturno. Si la acción moviera solo
uno, el hallazgo seguiría figurando **igual que antes de toda la migración** —
el mismo síntoma de siempre, ahora adentro del modelo nuevo.

Se juntan por **(SUJETO, CAUSA)**, que es lo único que comparten de verdad:
`Accion.causa` **ES** la regla del detector, declarada para el eval set (§0.av).
Sin eso haría falta una tabla de equivalencias, que es otra cosa que se
desincroniza sola.

> La solución de fondo es que los dos caminos acuerden identidad. Esto es el
> puente honesto mientras tanto, y **no esconde el problema**: los dos objetos
> siguen existiendo y se pueden contar.

#### Y se va la primera de las cinco derivaciones

`atendido` consultaba `av_agent_propuestas` por su cuenta (`_aplicados_ok`).
Era **una de las cinco derivaciones ad-hoc** que motivaron toda la migración
(§0.bc) — las que se contradecían entre sí sin fallar nunca.

Ahora sale del objeto y viene en el mismo JOIN: **una query menos y una fuente
de verdad menos.**

    en_curso · resuelto  → «aplicado»
    visto · ya_votado    → «votado»
    sin item             → pendiente (ante la duda, es trabajo)

    DERIVACIONES AD-HOC   ████░  4 quedan (recien · ya_votado · sin_puerta · vencido)
    MIGRACIÓN             █████░░░░░░░  5/12


### 0.bi EL RELOJ NO TENÍA CUERDA (2026-08-21)

`en_seguimiento()`, `Item.confianza_del_arreglo` y el escalonado 1·2·3·7·14·30
quedaron construidos ayer… y **no los llamaba nadie**. Cero apariciones fuera de
su propio módulo.

Es la enfermedad que este proyecto ya tiene bautizada —*una tarea existe solo si
alguien lee su salida* (§0.l)— y me la volví a comer construyendo justo la pieza
que más importa. Un reloj sin cuerda es un adorno.

**Enchufado en los dos extremos:**

    jobs/seguimiento (23:50, diario)  →  le da cuerda
    la vista del agente               →  lo muestra

#### Qué vota, y sobre todo qué NO

    pasó los 30 días sin volver   → ✔ `verificado`
    VOLVIÓ                        → ✖ `verificado`, con el motivo real

Los dos van con `origen='verificado'`, que la compuerta de autonomía cuenta **a
la par de un voto humano** (§0.f). Y con razón: un ✔ tuyo es una opinión; que un
problema no haya vuelto en 30 días no lo es. El agente no controla eso.

⚠️ **«Todavía no volvió» NO es «aguantó».** Solo vota el que pasó el ÚLTIMO
hito; los del medio siguen en prueba. Premiar a los tres días sería exactamente
lo que el escalonado vino a evitar.

⚠️ **Idempotente por `ref`.** El job corre todos los días y lo que aguantó sigue
aguantando: sin eso, en un mes un solo arreglo tendría 30 votos que son uno.

#### Y en la pantalla

    EN PRUEBA   AL30  pata_equivocada   2/6 hitos · próximo a los 3d
    VOLVIÓ      GD46  sin_ejes          ← el arreglo no era el bueno

Lo que volvió va primero: es lo único accionable de esa lista.

> Con esto el ciclo cierra por primera vez de punta a punta: el control
> encuentra → apretás el arreglo → pasa a `en_curso` → el detector no lo ve más
> → `resuelto` y arranca el reloj → al día siguiente el agente puede decir, **por
> su cuenta**, si el arreglo sirvió.


### 0.bj LA IDENTIDAD DE UN PROBLEMA: (qué cosa, qué le pasa) (2026-08-21)

El puente de §0.bh —mover los dos objetos que el mismo bono roto generaba—
funcionaba, y era **la respuesta a un problema que no había que resolver sino
eliminar**. La causa estaba una capa más arriba: la clave de un item incluía
`tipo` y `origen`.

    detector →  precio_moneda|live|bpoa7|pata_equivocada
    control  →  control|control:patas_equivocadas|bpoa7|patas_equivocadas

Un bono con la pata mal cargada. **Dos objetos, porque lo miran dos cosas
distintas.**

> **`tipo` y `origen` no son la identidad: son QUIÉN LO VIO.** Que un problema
> lo vean dos no lo convierte en dos problemas. Un problema es
> **(QUÉ COSA, QUÉ LE PASA)** — el resto son atributos.

`core.ciclo.identidad(sujeto, causa)` es ahora el único lugar donde se arma, y
`av_agent_items.clave_de_problema()` la envuelve normalizando la causa.

#### Las dos guardas que no son obvias

**El sinónimo se DERIVA, no se lista.** El control se llama
`patas_equivocadas` y el detector emite `pata_equivocada`: la misma causa con
dos nombres. El mapa sale de `ACCIONES` —donde cada acción ya declara `sobre`
(el control) y `causa` (la regla del detector con la que vota al eval set)— y no
de una tabla aparte. Una tabla aparte sería una segunda opinión sobre quién es
quién: el día que difiriera del voto, ni la pantalla ni el eval set fallarían,
simplemente contarían distinto (REGLA #9).

**El que no tiene sujeto.** Un `db_cambio` habla de la base entera, no de un
bono. Sin sujeto, todos los sin-sujeto de una misma causa colapsarían en UN
objeto y se taparían entre ellos, así que ahí —y solo ahí— el origen vuelve a la
clave como respaldo.

#### Lo que se BORRÓ, que es la mitad del trabajo

- `av_agent_items.marcar_por()` — el puente de §0.bh. Sobra: los dos escriben en
  el mismo objeto.
- `av_agent.clave_de_hallazgo()` — no la llamaba nadie y guardaba la fórmula
  vieja. Una segunda opinión sobre la identidad esperando a que alguien la use.

Dejarlas "por las dudas" es cómo vuelven los dos objetos por otro camino.

#### UN SOLO RELOJ para las dos pantallas

Mientras el objeto estaba partido, **AHORA podía decir «recién» y ENCONTRÓ «11
días» del mismo problema.** El centinela tenía su `abierto_at` y el censo el
suyo. Ahora `estado()` lee la antigüedad del objeto por `LEFT JOIN` —en la MISMA
query, que el peaje de Supabase se paga por viaje— y publica `dias_abierto`, así
las dos pantallas no pueden contradecirse. Si el objeto todavía no existe (un
hallazgo de este mismo ciclo, antes de espejarse) cae a la local: en ese instante
es lo mismo.

#### La migración de lo que ya estaba escrito

Los 64 items del backfill y la columna `hallazgos.clave` quedaron con la
identidad vieja — memoria **existiendo e inalcanzable**, el modo de falla que
este mismo script se cuida de no provocar. `scripts/backfill_items` los
re-identifica desde sus propias columnas en vez de borrarlos (borrar tiraría la
antigüedad real, que es lo único que ese script vino a rescatar), y cuando dos
caen en la misma clave los **fusiona**: fecha de apertura más vieja, suma de
veces y **gana el estado más ABIERTO** — si uno lo daba por resuelto y el otro
no, el problema no está resuelto, y cerrarlo por una migración lo haría
desaparecer de la pantalla en silencio.

> El choque de claves durante la fusión **es el éxito de la migración**, no un
> error: son los dos objetos del mismo problema encontrándose por fin.



### 0.bk LO QUE EL AGENTE MANDA TAMBIÉN ES UN OBJETO (2026-08-21)

El modelo cubría lo que el agente **encuentra** (hallazgos, controles,
centinela). Lo que **manda** seguía crudo: `av_agent_avisos` con su `resuelto
bool`, `av_agent_aviso_items` con su `hecho bool`, `av_agent_preguntas` con su
`estado text`. Tres formas más de decir lo mismo, y ninguna con memoria.

Y no son otra cosa. *«A este bono le falta el CER de emisión»* es
**(qué cosa, qué le pasa)**: la misma identidad de §0.bj. Tanto, que cuando el
detector nocturno encuentra ese mismo dato faltando **los dos escriben en el
MISMO objeto** — que es exactamente lo correcto: es un problema visto dos veces,
no dos problemas.

#### Fila por fila, no el aviso entero

El aviso de saldos se rearma en cada corrida, así que agrupado nunca podría
decir lo único que importa:

    la cuenta 805 lleva CUATRO DÍAS descubierta en ARS
    …y volvió a estarlo tres días después de que la cerraste

Por eso el espejo es de `av_agent_aviso_items`: cada fila con su antigüedad y su
`veces`.

#### Tres decisiones que no son obvias

**Reabrir es `volvio`, no «nuevo».** Destildar una fila o deshacer un aviso
significa que alguien lo había dado por hecho y el pendiente sigue. Marcarlo
`nuevo` borraría esa vuelta, que es justo lo que el seguimiento escalonado tiene
que contar (§0.bi): volver una vez borra la confianza acumulada.

**La identidad NO sale de partir la clave.** La clave de una pregunta es
`falta:TZXD8` — causa y sujeto pegados con dos puntos, un `split(':')` de
distancia. Se agregaron **columnas** `sujeto` y `causa`: atar la identidad a una
convención de texto es REGLA #9, y el día que un sujeto traiga `:` adentro
partiría mal y en silencio. Una pregunta sin esos campos **no se espeja**:
prefiero que le falte la memoria a que la tenga mal — un objeto con el sujeto
equivocado es peor que ninguno, porque se lee como cierto.

**El espejo nunca puede tumbar la lista.** Las tablas originales no se tocaron:
siguen siendo las que dibujan la pantalla con su campo para tipear, su
vencimiento y su dueño. Si el objeto no se puede escribir, el aviso se manda
igual. Cambiar una funcionalidad que anda por una que estamos estrenando sería
al revés de todo.

#### La barra de progreso decía 1/12 y era mentira hacia abajo

Cinco tablas ya tienen su objeto con historia y solo les falta sacar la columna
vieja — que es riesgo puro y ningún beneficio nuevo. Contarlas como cero pinta
un proyecto que no arrancó; como hechas, uno terminado. Ahora son tres estados:

    MIGRACIÓN  █▓▓▓▓▓░░░░░░  1 hecha · 5 con objeto · 6 crudas (de 12)

Y el conteo sale de un **campo** (`Forma.espeja`), no de buscar la palabra
«espeja» adentro del texto que la describe: una barra que se calcula leyendo un
comentario miente el día que alguien reescribe el comentario. El mismo día un
test de `test_ciclo` se cayó por eso mismo —afirmaba `"redundante" in f.como`—
y se reescribió para probar el árbitro (`f.leer`) en vez de su descripción.



### 0.bl NO TODO LO QUE TIENE ESTADO ES UN PROBLEMA (2026-08-21)

La barra de deuda decía **11 tablas a migrar** y estaba mal en algo peor que el
número: apuntaba a un objetivo equivocado. Metía en la misma bolsa cosas que no
son la misma cosa.

| clase | qué es | por qué NO se migra |
|---|---|---|
| `problema` | algo está mal en algo | — sí se migra, es la canónica |
| `sensor` | una lectura cruda (`proveedor_estado`: ¿contesta Aunesa?) | el objeto lo hace el detector que la lee. Migrarla sería confundir el **termómetro con la fiebre** |
| `bitacora` | que algo CORRIÓ (`av_agent_runs`, `av_agent_propuestas`) | su `estado` describe la corrida, no un problema, y su valor es ser append-only |
| `meta` | habla DE los items (`av_agent_seguimiento`) | el modelo se contendría a sí mismo |
| `acuse` | quién LEYÓ qué, por persona (`salud_vistos`) | `items.visto_at` es **uno solo para todos**: migrarla haría que el segundo admin no viera nunca el modal que cerró el primero |

Cinco de las once. La deuda real de modelo eran **seis**, y con esta tanda las
seis tienen objeto:

    MIGRACIÓN  █▓▓▓▓▓▓  1 canónica · 6 con objeto · 0 crudas

> Ojo con lo que eso significa y con lo que no: **ya no queda un problema sin
> memoria.** No dice «migrado» — las columnas viejas siguen ahí, y sacarlas es
> riesgo puro sin beneficio nuevo, así que se harán de a una cuando toque.

#### El «no me interesa», que estaba a medias

`av_agent_ignorados` sacaba el hallazgo de la pantalla y **el objeto seguía
abierto**, sumando `veces` y antigüedad de algo que el user ya descartó. La
pantalla decía «no hay nada» y el contador «lleva 20 días»: dos verdades sobre
lo mismo, REGLA #9 en su forma más visible.

`ignorar_sujeto()` apaga todos los objetos de ese ticker. **Por sujeto y no por
causa**, igual que la tabla: si un papel no interesa, no interesa en ninguna de
sus formas — ver que «le faltan los flujos» a un bono que ya descartaste es el
mismo ruido con otro nombre.

⚠️ **No toca los RESUELTOS.** Ignorar es «no me lo muestres más», no «borrá su
historia»: un arreglo en seguimiento tiene que seguir contando sus hitos
(§0.bi). Y desandar devuelve solo los que están `ignorado`, nunca revive algo
que se cerró por otro motivo.

⚠️ No confundir `ignorar_sujeto` con el puente borrado en §0.bj. Aquél juntaba
dos objetos que eran **el mismo problema** y estaba tapando un defecto de
identidad. Éste es una semántica real: el sujeto entero deja de interesar.



### 0.bm DE 64 ABIERTOS, ¿CUÁLES PIDEN ALGO HOY? (2026-08-21)

El modelo guardaba `veces`, `abierto_at` y `vuelto_at` desde §0.bd… **y la
pantalla seguía ordenando por severidad**, o sea igual que antes de tener
memoria. Sesenta y cuatro cosas abiertas, todas iguales, para siempre — que es
literalmente la queja: *«las cosas en ENCONTRÓ siguen figurando»*.

> **Un tablero que no prioriza no es un tablero, es un depósito.** Toda la
> migración de §0.bd a §0.bl construyó la memoria; esto es lo primero que la
> LEE para decidir.

#### Las cuatro bandas, y el número que decide

    volvio      el arreglo FALLÓ — alguien ya lo dio por resuelto y volvió igual
    estancado   lo viste, sigue abierto, y hace días que no pasa nada
    arrastra    lleva días abierto y NADIE lo miró todavía
    nuevo       apareció hoy

    → de 64 abiertos, 3 PIDEN ALGO

`nuevo` **no** cuenta en ese número: apareció hoy y todavía no probó que sea
algo. Si entrara, el contador subiría y bajaría solo — y un número que se mueve
sin que pase nada deja de mirarse, que es cómo murieron los tableros anteriores.

`volvio` gana sobre todo, incluso sobre una severidad alta de hace un mes: nada
informa más que un arreglo que falló. Y se detecta por `vuelto_at` y no solo por
el estado, así no se pierde justo cuando alguien lo marcó visto.

La prioridad es una **tupla**, no un puntaje. Un «87 puntos» no se puede
discutir ni auditar y esconde cuál de los criterios lo puso ahí.

#### La banda que NO está, y por qué

Estaba servida: comparar `veces` contra las corridas transcurridas y separar
*estructural* de *intermitente*. **No se puede desde acá**: el centinela corre
cada 5 minutos y el control una vez por noche, así que 18 veces significa cosas
opuestas según quién lo vio. Inventar ese denominador habría dado un cartel con
pinta de medición y sin medición atrás (REGLA #2). Si volvió, `volvio` ya lo
dice con certeza. Hay un test que lo congela.

#### «Sigue roto» y «nadie lo miró» no son lo mismo

Y hasta acá se veían idénticos: los dos son una fila abierta. La diferencia se
DERIVA de lo que ya está guardado — si el origen volvió a correr (hay otro
objeto suyo con `ultimo_at` más fresco) y a éste no lo refrescó, el detector
pasó y **no lo evaluó**. Es el punto ciego que la guarda `evaluados` (§0.be)
evita cerrar por las malas, ahora mostrado en vez de simplemente no-cerrado.

Se compara contra **su propio origen**: medir al control nocturno con la vara
del centinela lo marcaría como abandonado todas las mañanas.

#### Dónde se ve

Tab AHORA, arriba del seguimiento — primero qué hay que hacer, después si lo que
ya se hizo aguantó. Y `scripts/diag_importa` lo imprime en el Droplet: la
primera pregunta después de una migración es siempre *«¿y esto qué muestra
ahora?»*, y contestarla mirando la app mezcla dos cosas que fallan por separado
(el dato y el render).

#### De yapa, dos cosas que la corrida en prod dejó a la vista

- **`SyntaxWarning: invalid escape sequence '\s'`** en `av_agent_hacer.py`, en
  cada arranque de la API y en cada job. El docstring de `_nombre` **cita** una
  regex para explicar por qué ese patrón matcheaba de casualidad; sin `r"""`,
  Python lo canta como error de sintaxis. Ruido permanente en los logs por un
  comentario. Barrido: era el único del repo.
- **El marcador de `diag_ciclo` se contradecía con su propio resumen**:
  `salud_vistos` y `proveedor_estado` salían con `░` («todavía dice lo suyo»)
  tres renglones debajo de haber explicado que ésas no se migran. Ahora llevan
  `·` y su clase al lado.



### 0.bn «YA LE MARQUÉ MIL VECES Y NO HACE NADA» (2026-08-21)

El user, sobre `job:controles_datos` en SALUD: *«ya le marqué mil veces que sí
sirve verlo pero no hace nada… sigue estando estático ahí como si nada»*.

Tenía razón, y **eran tres cosas distintas que se ven como una sola**.

#### 1. El voto se guardaba y la pantalla no podía verlo. NUNCA.

    ya_votados()   →  WHERE origen = 'humano'
    la observación →  se guarda con  origen = 'utilidad'

Cero errores, cero logs. Los botones volvían intactos en cada recarga, para
siempre, hubiera votado una vez o cincuenta.

La causa de fondo ya tiene nombre en este repo: **un filtro sirviendo a dos
preguntas.** `utilidad` se separó de `humano` para que las observaciones no
inflen la compuerta de autonomía (§0.f) —y eso está bien—, pero *«¿esto cuenta
para dar autonomía?»* y *«¿ya me contestaste?»* no son la misma pregunta, y
quedaron compartiendo un `WHERE`. La compuerta tiene que ser estricta; la
pantalla tiene que acordarse de TODO lo que contestaste.

**El mismo bug otra vez, un nivel más abajo**: `_voto_previo` —el que evita
guardar dos veces la misma respuesta— también tenía el origen clavado en
`'humano'`. Para una observación el previo nunca aparecía, así que cada
«✔ sirve» escribía una fila nueva. La dedup existía y no dedupeaba nada.

#### 2. «✖ ES RUIDO» era un botón que no hacía nada

Medido: **cero consultas** en todo el repo leían esos votos. Se escribían y la
fila quedaba exactamente donde estaba.

> Un botón que guarda una opinión y no cambia nada es **peor** que no tenerlo,
> porque parece que hizo algo. Eso es lo que enseña a desconfiar de la pantalla
> entera.

Ahora la fila se marca y la tab la esconde, con las tres guardas de siempre: el
número **siempre a la vista** (`N dijiste que es ruido`), un clic la destapa, y
la búsqueda la encuentra aunque esté oculta. La fila **viaja igual** en el
payload — sacarla la volvería irrecuperable desde la app, y esconder sin poder
volver atrás es cómo se consigue que nadie marque nada.

#### 3. Y en esa fila NO HAY ningún botón de «hecho»

Los tres que tiene son ANALIZAR (diagnóstico), ¿TE SIRVE VERLO? (una encuesta
sobre el AGENTE) e IGNORAR. Lo que se lee como «marcar hecho» es literalmente
una pregunta de opinión — el propio tooltip dice *«tu voto no cambia nada del
sistema: mide al agente»*.

Y la fila **está bien que esté**: `controles_datos` falló el 20/08 16:30 y sigue
fallando (2d ×8). El detector no se equivoca. Lo que estaba mal es que después
de tocarla se viera idéntica.

> **La lección, que es la misma de §0.bj y §0.bl:** cuando un filtro empieza a
> servir a dos preguntas, se parte en dos. No falla nada el día que se
> desalinean — simplemente una de las dos empieza a contestar mal, con total
> seguridad, y solo se descubre cuando alguien se harta de la pantalla.



### 0.bo AHORA ES EL DÍA DE HOY. ENCONTRÓ ES LA COCINA. (2026-08-22)

La tab decía **AHORA 1** y abajo mostraba un control abierto hacía 21 horas,
con 131 filas plegadas, 40 resueltas y el seguimiento de arreglos viejos. El
user: *«no entiendo cuál es la diferencia entre AHORA y ENCONTRÓ si está todo
mezclado»*.

**No la había.** Las dos tabs eran el mismo backlog acumulado con nombres
distintos — una lo juntaba desde el centinela y la otra desde el censo. Por eso
la pregunta no tenía respuesta.

> **AHORA** informa. Lo que pasó HOY y el latido. Cero botones de trabajo,
> cero listas plegadas, cero acumulado. *«Es solamente informativo y JUSTAMENTE
> NO PUEDE FALLAR.»*
>
> **ENCONTRÓ** es la cocina: todo lo abierto, con sus herramientas.

#### Las tres novedades, y nada más

    VOLVIÓ         se había arreglado y volvió — lo que más informa de todo
    APARECIÓ HOY   no estaba ayer
    SE ARREGLÓ     cerró solo. No pide nada: está para que lo sepas

Tres decisiones que no son obvias:

**`se_arregló` NO suma al contador.** Es una buena noticia, no algo que pida
atención — si sumara, el número de la tab subiría cuando el sistema *mejora*.

**Lo que volvió no se cuenta dos veces.** Nació hace tres días y volvió hoy: es
UNA novedad («volvió»), no también «apareció».

**El corte es en hora ARGENTINA.** El día UTC arranca a las 21:00 de acá: con
el corte en UTC, algo de las 21:30 de anoche saldría como «de hoy» junto con lo
de esta mañana. Dos días mezclados bajo el mismo rótulo, justo en la tab que no
puede fallar.

Y lo hace el BACKEND, no la pantalla: el navegador no puede mirar el reloj
mientras dibuja, y el criterio tiene que ser uno solo para las dos tabs.

#### Lo que se mudó, y por qué no se borró

`qué pide algo hoy` · `¿los arreglos aguantan?` · `viene de antes sin ver` ·
`ya vistos` · `se arreglaron solos` → **todo a ENCONTRÓ**, plegado y arriba de
la lista.

Ninguna era del día: todas eran el acumulado con otro nombre. Pero borrarlas
habría dejado **131 cosas abiertas sin ninguna pantalla**, que es peor que el
desorden — por eso el backlog del centinela vive ahora en `VigilanciaAbierta`,
con su botón de marcar visto intacto.

#### El latido, en una línea

El título **REVISANDO** + la cadencia ocupaban más que las novedades que tenían
que anunciar, y repetían lo que ya dice la barra de arriba (`censo hace 52 min ·
vigilando cada 30s`). Queda el punto verde y nada más: la cadencia, los ciclos y
el «se apaga en N s» son datos del MECANISMO y viven en el `title`.

Lo único que sube a la línea es lo que sí cambia algo: **que esté apagado**,
porque entonces el silencio de abajo no vale nada.

#### Dos guardas para que esta tab no mienta

- **Sin el corte del día no se inventa un día.** Si el backend viene viejo
  (deploy desparejo), la tab dice que no puede separar lo de hoy y manda a
  ENCONTRÓ. Decir «hoy no pasó nada» sin haber podido mirar es exactamente la
  falla que esta tab no puede tener.
- **Una fecha rota no cuenta como de hoy.** Ante la duda, afuera: meter basura
  en la lista del día es peor que no mostrarla, porque acá se lee como «esto es
  lo que pasa».

#### Y el contador contaba otra cosa que la lista

`nAhora` sumaba `cent.sin_ver` —el backlog sin ver— mientras la tab dibujaba
otra cosa. De ahí el «AHORA 1» con una fila de 21 horas abajo: **el número y la
lista hablaban de conjuntos distintos.** Ahora cuenta las novedades del día, que
es lo único que la tab muestra.



### 0.bp ENCONTRÓ, CON EL MENÚ DE SKILLS (2026-08-22)

Al mudar la cocina a ENCONTRÓ (§0.bo) quedaron **tres cajas colapsables
apiladas** arriba de la lista, todas plegadas: la pantalla mostraba tres
títulos y ningún contenido. El user: *«queda horrible… quiero que quede como lo
de SKILLS, las mains horizontales y las opciones abajo. Es fundamental la UX/UI
porque si no es inentendible»*.

Es **el mismo error que SKILLS ya había resuelto** con su menú de dominios:
apilar secciones obliga a scrollear para saber qué hay.

    131 hallazgos   85 por resolver · 17 ya hechos    acá se arregla — lo de hoy está en AHORA

    LA LISTA 131    QUÉ PIDE ALGO 59   ¿AGUANTAN? 4   VIGILANCIA 0
    85 por resolver   de 61 abiertos      en prueba      todo visto
    ───────────────

Mismo formato que SKILLS y por las mismas razones:

- **La submétrica debajo de cada entrada no es decoración**: es lo que deja
  elegir a dónde ir *sin entrar*. Un menú con solo el número obliga a probar las
  cuatro.
- **Se mira UNA por vez.** La lista, la priorización, el seguimiento y el
  backlog del centinela no compiten por el mismo scroll.
- **El número dice por qué entrarías, no cuántas filas hay.** En LA LISTA la
  submétrica es `por resolver` (ni atendido ni descartado), no el total.

#### Dos cosas que se rompen si no se miran

**La barra de filtros ahora cuelga de su sub-tab.** Sin ese corte quedaba a la
vista mientras mirabas el seguimiento — filtrando algo que no estaba en
pantalla.

**Y perdió el `-mt-4`.** Ese tirón existía para pegarla al borde del panel
cuando era el PRIMER elemento de la tab; abajo del menú, la montaba encima de
las sub-tabs. Sigue *sticky*, porque con 130 filas que la barra se vaya de
pantalla es peor.

#### Y los tres paneles dejaron de plegarse

Adentro de una sub-tab, un panel que además hay que desplegar son **dos clics
para ver lo que ya elegiste ver**. El pliegue tenía sentido cuando competían
por la pantalla principal; ahora que cada uno tiene su lugar, sobra.



### 0.bq EL NOMBRE LEGIBLE YA ESTABA ESCRITO (2026-08-22)

Tres quejas del user, y las tres eran la misma enfermedad de siempre.

#### 1. «control:comitentes_sin_nive…» — el título existía hace meses

La fila mostraba el **ID** del control, cortado a la mitad en una columna
angosta. El user pidió *«un LLM que asigne títulos más sencillos»*.

**No hace falta.** Cada control declara su nombre humano desde que se dio de
alta:

    control:patas_dolar_sin_ped…   →   Patas en dólares que nadie pide
    control:comitentes_sin_nive…   →   Comitentes activos sin nivel 1
    control:fci_incompletos        →   Assets FCI sin ticker/emisor

`salud._chequeos_controles` armaba el título con `cid.replace("_", " ")` —
fabricaba uno feo teniendo el bueno al lado, en `CONTROLES[].titulo`. Y para los
chequeos de SALUD el título **ya viajaba en la evidencia** y la pantalla no lo
leía.

> Es la misma falla que este proyecto ya se comió con el eval set, con el
> seguimiento y con los votos de utilidad: **el dato existe, se guarda, y nadie
> lo lee.** Antes de agregar un modelo, buscar el campo.

El nombre para pantalla lo resuelve el BACKEND (`h["nombre"]`), no el
navegador: si lo decidiera cada vista, dos pantallas nombrarían distinto al
mismo hallazgo.

#### 2. Lo YA HECHO se va de la lista de trabajo

*«Si algo ya está hecho tiene que salir de acá y en todo caso pasar a esto de
que se controla si se volvió a romper.»* Exactamente: lo que atendiste **no es
trabajo pendiente**, es un arreglo esperando confirmación — que es literalmente
lo que mide ¿AGUANTAN?.

Antes era un toggle (`17 YA HECHOS`) y las filas seguían en la lista, en gris.
Ahora salen y aparecen en ¿AGUANTAN? con la distinción que importa:

    ✔ arreglado   se aplicó: el dato cambió
    votado        lo miraste, pero el dato sigue igual

Mezclarlas haría que «17 hechos» incluya diecisiete cosas que siguen rotas.

**La búsqueda igual los encuentra**: tipear el ticker de algo que arreglaste y
que no aparezca sería esconderlo, no ordenarlo.

Y **el número de LA LISTA pasa a ser lo que falta hacer**, no el total: decía
132 con 17 ya hechos adentro, o sea prometía más trabajo del que había.

#### 3. El cuadro de texto que repetía el menú

    132 hallazgos  115 por resolver · 17 ya hechos    acá se arregla — lo de hoy está en AHORA

Un renglón entero diciendo lo que el menú de abajo dice dos centímetros más
abajo, con sus cuatro números. Se fue. La frase que explica la pantalla se
aprende una vez; no hace falta todos los días.



### 0.br HORA ARGENTINA, ORDEN Y UNA COLUMNA DE CUÁNDO (2026-08-22)

*«Basta de UTC y esas cosas… horario argentino mostrar.»* Tenía razón en dos
lugares, y uno era peor que el otro.

**El mensaje que decía la hora equivocada.** `salud` imprimía
`ultimo_t.strftime(...)` sobre un datetime en UTC **y le pegaba la etiqueta
«UTC»**: «la corrida de 20/08 16:30 UTC falló», cuando acá eran las 13:30. No
es solo la etiqueta — pedirle a alguien que reste tres horas mentalmente para
ubicar un hecho es garantizar que lo ubique mal. El formateo pasa a vivir UNA
vez, en **`core.tz.hora_ar`**, que ya existía y esa capa no usaba.

**Y la pantalla heredaba la zona del navegador.** `hora()` llamaba a
`toLocaleTimeString("es-AR")` **sin `timeZone`**: en la oficina coincide con ART
*por casualidad*, y desde un teléfono en otra zona la pantalla miente sin
avisar. La zona se declara, siempre.

#### La columna de CUÁNDO

El motivo ya traía un `· 12:25` pegado al final del texto. Ahí no se puede
barrer ni ordenar: hay que leer la frase entera de cada fila para ubicarla.
Como columna, el ojo la recorre de una.

De HOY muestra la hora; de otro día, la fecha — repetir «22/08» ciento treinta
veces gasta ancho sin informar. El `title` siempre trae las dos.

Para eso `abierto_at` **deja de descartarse** en la vista (se leía del JOIN,
se usaba para `dias_abierto` y se tiraba). Va en ISO y no formateado: mandarlo
ya escrito parece más simple y es peor — el mismo dato no se podría ordenar sin
volver a parsear el texto.

#### Orden y densidad

Las filas venían **en el orden que devolvía la query**, o sea ninguno: lo que
apareció recién quedaba enterrado entre lo de la semana pasada. Ahora es más
reciente primero, y lo que no tiene `abierto_at` va al final — no se le inventa
una fecha para poder ordenarlo.

El alto de fila baja de `py-1` a `py-0.5`: con 130 filas, cada 4px son media
pantalla.



### 0.bs REGLA #1 CUBRÍA `api/` Y DEJABA `scripts/` AFUERA (2026-08-22)

    ImportError: cannot import name 'get_ultimo_mep' from 'core.dolar_sql'

Un `git pull`, un deploy y un traceback — por un símbolo. La función vive en
`api.services.macro` y yo escribí el import sin resolverlo.

El hook de pre-push valida `from api.main import app`, que es lo que tumba la
API entera. **Un script se descubre roto cuando el user lo corre en el
Droplet**, o sea en el peor momento posible: después de pullear y deployar.

Y no lo agarra nada de lo que ya había:

- **`ruff`** es análisis estático de nombres: no resuelve el módulo.
- **`import scripts.x`** tampoco, porque los scripts importan **adentro de
  `main()`** a propósito, para no pagar el arranque de la app en un diag.

`tests/unit/test_scripts_imports.py` los resuelve a mano: recorre el AST de
cada archivo de `scripts/`, importa el módulo y verifica que el símbolo exista.
**139 scripts** cubiertos, ~12 s.

Tres detalles que lo hacen usable y no ruido:

- **Solo lo NUESTRO** (`api`, `core`, `jobs`, `engines`, `quant`, `scripts`,
  `config`). Un `openpyxl` ausente en el contenedor de CI no es un import roto
  — es una dependencia opcional, y fallar por eso convierte al test en algo que
  alguien va a saltear.
- **El submódulo no es un atributo.** `from core import curvas_sql` no tiene
  `hasattr` hasta que alguien lo importa: se reintenta como módulo antes de
  cantar falla.
- **Un parametrizado sobre lista vacía pasa en verde sin mirar nada** — el
  mismo «no pude» disfrazado de «está bien» que el agente persigue en sus
  detectores. Hay un test que exige que la lista tenga scripts.

Verificado al revés, que es lo único que prueba que sirve: con el import viejo
puesto, el test **falla nombrando la línea y el símbolo**.

> **De paso, CI estaba ROJA en `main` y no por esto**: `jobs.mayor_sync` entró
> en `a67f7153` con dos líneas de cron y sin registrarse en el árbol del
> Diagnóstico, y `test_jobs_registro_matchea_crontab` lo cantaba. Queda
> registrado — es el otro lado de la conciliación bancaria
> (`interbanking_sync` trae lo que dice el BANCO, `mayor_sync` lo que dice
> CONTABILIDAD) y su falla es de las silenciosas: la tab no miente, se queda
> quieta.



### 0.bt «ME DICE DE UNO SOLO» — medido, y la hipótesis era equivocada (2026-08-22)

El user, viendo la grilla de los BPO toda en pesos y el agente cantando tres:
*«están literalmente todos cotizando en pesos y me dice de uno solo… acá hay
algo desconectado de lo que pasa de verdad»*.

`detectar_precio_fuera_de_moneda` tiene **seis puertas en fila** y cualquiera
puede estar dejando pasar al bono por motivos **opuestos** — una sería un bug y
otra el comportamiento correcto. Desde el código no se puede decir cuál.
`scripts/diag_pesos_no_detectados` las recorre en el mismo orden y dice, bono
por bono, cuál lo frenó.

    1_no_es_curva_usd          69
    2_dolar_linked             31
    3_sin_precio                9
    4_ya_viene_en_dolares     101
    6b_CANTA_pata_equivocada    6   ← BPOA7 BPOA8 BPOB7 BPOB8 BPOC7 GD46
    6c_NO_CANTA                11

#### Dos cosas, y las dos importan

**El detector SÍ agarra a los BPO.** Los cinco están en la lista. La foto de la
pantalla tenía tres y otros — entre medio se aplicó el arreglo a esos tres, y
`3_sin_precio` recuerda que **el detector solo ve lo que tiene precio en ese
instante**, así que la lista crece durante la rueda. La queja apuntaba a un
detector ciego y el detector no lo es.

**Pero hay un agujero real de 11, y no es el que yo apostaba.** El docstring de
mi propio diag decía que la causa sería `6d` —el ticker sin pata default en
`mercado.especies`, porque `BPOA7 → BPA7D` pierde una letra del medio y rompe
cualquier regla de string (REGLA #9)— y la medición dio **`6d` = 0**.

Los once son **`6c`**: el master **ya apunta a la pata que `especies` marca como
default**, y esa pata igual cotiza en pesos. Son todas ONs corporativas
(`CP36O`, `LOC6O`, `PECNO`, `VSCYO`…).

> Y el script decía, en su primera versión, *«se arregla completando
> `mercado.especies`»*. **La medición lo desmintió y esa frase se borró**, no se
> matizó: dejarla escrita mandaba a corregir lo que no está roto. Un diag que
> conserva su hipótesis después de refutarla es peor que no tenerlo — se lee
> como conclusión.

#### La pregunta que decide el arreglo

    ¿existe una pata en dólares para estos once?

      NO existe  → el bono cotiza en pesos y punto. No hay nada que arreglar;
                   lo discutible es por qué está en una curva USD.
      SÍ existe  → `es_default` elige mal. Primary marca la más OPERADA, que no
                   es la que necesita una curva en dólares. Ese sí es un dato
                   mal cargado, y con arreglo.

El diag ahora la contesta: lista todas las patas de cada uno con su moneda, su
plazo y cuál es la default, y los parte en los dos grupos.



### 0.bu LA COMPARACIÓN ERA CIRCULAR: `es_default` es una COPIA DEL MASTER (2026-08-22)

El detector de `pata_equivocada` cruzaba dos fuentes que **no son dos**:

    curvas.instrumento          ← lo que el master suscribe
    especies.es_default         ← ¿de dónde sale?

De acá, en `scripts/sembrar_especies`:

```python
filas.append({**p, "es_default": p["simbolo"] == actual})   # actual = curvas.instrumento
```

**`es_default` no se deriva de Primary: es una copia de lo que el master ya
usa.** El detector comparaba el master contra sí mismo. La comparación era
**vacía por construcción** y solo se disparaba cuando el seeder había quedado
viejo respecto de un cambio manual — que es exactamente lo que pasó con los 6
BOPREAL, y por eso parecía que funcionaba.

> Y el `CLAUDE.md` decía *«`mercado.especies.es_default` se deriva de
> Primary»*. **El doc y el código se contradecían**, que es la mitad de por qué
> esto duró: yo mismo escribí el detector creyéndole al doc.

#### Lo que la medición mostró

`scripts/diag_pesos_no_detectados`, en prod: **11 bonos** con precio en pesos
en curva USD, con pata en dólares existente y validada, y el detector callado
en los once. Los once con la misma forma:

    ★ VSCYO   ARS  24hs   ← es_default (o sea: lo que el master usa)
      VSCYD   USD  24hs   ← la que le corresponde a una curva en dólares
      VSCYD   USD  CI
      VSCYO   ARS  CI

#### El criterio correcto, que ya estaba escrito

No es *«cuál es la default»* — eso es circular. Es **«cuál pata corresponde a
la MONEDA DEL EJE»**, y eso sí es independiente: sale de `especies.moneda`
(Primary) cruzada con `curvas.moneda_eje`.

Esa lógica **ya existía** en el seeder (su lista `cruzadas`, con
`preferencia`: MEP antes que cable, 24hs antes que CI) y **solo se imprimía por
consola**. Nadie la persistía y el agente no la leía. Se muda a
`core.especies.pata_para_el_eje` — una vez, para los dos.

⚠️ Se muda **entera, con su guarda**: `None` significa «no existe», no «está
bien». Una ON hard dollar que cotiza en su única especie NO está cruzada — no
hay a dónde apuntar. Esa condición es la que evitó 137 falsos positivos cuando
se escribió, y reescribirla desde cero habría sido volver a pagarlos.

`es_default` queda de **respaldo** para el ticker sin patas cargadas: peor
criterio, pero mejor que quedarse mudo.

> **La lección, y es REGLA #9 otra vez:** dos fuentes que se comparan tienen que
> ser INDEPENDIENTES. Cuando una es copia de la otra, la comparación no falla —
> **da siempre que está todo bien**, que es la peor forma de fallar. Acá el
> síntoma fue un detector que parecía andar porque acertaba en los casos donde
> la copia había quedado desactualizada.

#### Y el diag tenía el mismo bicho adentro

Después de arreglar el detector, la corrida en prod devolvió **exactamente los
mismos números**. No era que el fix no sirviera: **`diag_pesos_no_detectados`
reimplementaba las seis puertas, incluida la que decide.** Arreglé el detector
y el diag siguió midiendo su copia vieja.

> La herramienta que existe para cazar REGLA #9 **tenía REGLA #9 adentro**, y de
> la peor forma: no falló, contestó con seguridad usando el dato equivocado —
> y encima habría «confirmado» que el arreglo no servía.

Ahora el veredicto sale de `detectar_precio_fuera_de_moneda`. La caminata por
las puertas queda solo para EXPLICAR dónde cae cada bono, que es lo que un `for`
sobre los hallazgos no puede decir; pero **quién canta y quién no lo dice la
función real**, y por construcción ya no pueden divergir.

**La regla que queda: un diag que mide un comportamiento no puede
reimplementarlo.** Si lo reimplementa, no está midiendo el sistema — está
midiéndose a sí mismo.

#### VERIFICADO en prod (2026-08-22)

    6_CANTA_pata_equivocada   17    BPOA7·BPOA8·BPOB7·BPOB8·BPOC7·GD46
                                    CP36O·HJCLO·LOC6O·MGCOO·OLC7O·PECNO
                                    PFC3O·RC1CO·RCCRO·VSCYO·VSCZO
    6_NO_CANTA                 0

Los 6 que ya cantaba **siguen cantando** —el cambio de criterio no rompió
nada— y los 11 ciegos entraron con su símbolo exacto. Cero bonos llegan al
final del detector sin veredicto.

⚠️ **Lo que esto NO significa todavía**: el master sigue apuntando mal en los
17. El detector los VE; arreglarlos es apretar el botón, y **el efecto no se
mira hasta reiniciar el motor fuera de rueda** — el universo se arma al
arrancar (§0.u).



### 0.bv LA TAB CONTROL: «¿qué estás haciendo?» (2026-08-22)

> *«Estaría bueno que acá mismo, así como está SKILLS en una punta, ahí al lado
> haya una tab que se llame CONTROL y figure todo lo que el agente está
> monitoreando durante el día, actualización de la última vez y eso… Es como si
> viniera mi jefe y me diga qué estás haciendo y vea desglosado todo lo que
> hago. **De esa manera alguien puede ver fácil si hay algo que NO está
> haciendo.** No lo hagas cortado con alambre, hacelo bien. Y prolijo: poco
> texto, timestamps bien, las funcionalidades.»* — user

**La última frase es la que define el diseño.** El catálogo ya existía: SKILLS
contesta *qué sé hacer*. Lo que no había forma de contestar es *¿lo estoy
haciendo?* — y son dos preguntas distintas, porque **en una pantalla una
capacidad que nadie ejecuta se ve idéntica a una que corre cada cinco minutos**.
Ese es exactamente el hueco.

    SKILLS    qué sé hacer            catálogo
    CONTROL   qué estoy haciendo HOY  catálogo × crontab × job_runs × hallazgos

#### El eje es la RUTINA, no el dominio

Se agrupa por **job**, porque el job es la unidad que responde «¿corrió?». Un
detector no corre solo: corre adentro de un job, y **si ese job está caído, las
doce piezas que viven ahí están ciegas al mismo tiempo**. Agrupado por dominio
se verían doce filas rojas sin decir que son una sola causa — el mismo error de
lectura que SALUD arregló cuando la observabilidad vivía en seis pantallas.

#### Todo derivado. Ninguna lista nueva.

Que un detector nuevo **no** apareciera acá sería volver al problema: la
pantalla diría que el agente hace 30 cosas mientras hace 31, y nadie lo notaría.
Las cuatro fuentes ya existen y ya se mantienen solas:

| fuente | qué aporta |
|---|---|
| `av_agent_skills.catalogo()` | qué mira cada pieza y **en qué job corre** (`extra.corre_en`) |
| `jobs_catalogo` | el schedule REAL, leído de `deploy/crontab.txt` |
| `manager.job_runs` | cuándo corrió de verdad y cómo salió |
| `mercado.av_agent_items` | qué encontró y sigue abierto, por origen |

#### TRES estados, no dos: `atrasado` puede ser `None`

`atrasado` es `True` / `False` / **`None`**, y el `None` es el punto del diseño:
sin schedule legible o sin ninguna corrida registrada, decir «está al día» sería
inventar calma y decir «se atrasó» sería inventar una alarma. Se declara **sin
poder juzgar** y se pinta ámbar. Por eso el encabezado publica `sin_juzgar`
**siempre, aunque sea 0**: sin ese número, «0 atrasadas» se lee como «todo al
día» cuando puede ser «no pude mirar tres».

La tolerancia sale de **la cadencia del propio job** (`TOLERANCIA = 2.5 ×`), no
de una constante: un cron de 5 minutos y uno diario no se juzgan con la misma
vara.

#### Se cae con elegancia, y eso importa acá más que en otras pantallas

`catalogo_jobs()` joinea el crontab con `job_runs`, así que si Postgres no
contesta levanta entero — y **el día que la base está caída, la tab que existe
para decir «algo no está corriendo» sería la que menos dice**. Ahora cae al
crontab pelado (viaja con el deploy, se parsea sin tocar la base) y cada rutina
sale con `atrasado=None`: *sé qué debería correr, no sé si corrió*. Es la verdad
exacta y es útil; una pantalla en blanco no es ninguna de las dos.

#### Dos números que cierran la pantalla

- **`encontrados`** por rutina — lo que separa *corrió* de *sirvió*. Un job
  verde que hace un mes no encuentra nada puede estar mirando una tabla vacía.
- **`a_pedido`** — las habilidades que existen pero **no corren solas**
  (explicar un cálculo, mandar un mensaje). Va declarado porque si no, el que
  viene de SKILLS ve 56 allá y 32 acá y no sabe si faltan 24 o si 24 no corren.

El daemon del centinela se inserta a mano y primero: no está en el crontab y es
la pieza que más corre de todas, así que no aparecería nunca. **Lo que mira sale
de `av_agent_centinela._CUBRE`** —la misma lista que el propio `_observar()` usa
para decidir qué puede dar por cerrado— y no del cron `jobs.av_agent_live`, que
es otra cosa: al escribirlo con el cron le colgaba 8 piezas que el daemon no
corre y le faltaban las 2 que sí. Una lista que ya tiene otro dueño que la
mantiene es la única de la que uno se puede fiar.

Y el titular cuenta piezas **únicas**: un detector puede correr de verdad en dos
lados (el centinela mira `sin_precio` en rueda y el cron lo vuelve a mirar), así
que sale en las dos filas —correcto, corre dos veces— pero se cuenta una.

**Dónde**: `api/services/av_agent_agenda.py` · `GET /api/ia/av-agent/agenda`
(admin) · tab **CONTROL** del modal, pegada a SKILLS. El ⚙ de la derecha
(parada de emergencia + fuentes) conserva su lugar y pierde el nombre.

**Ley del agente (§0.o) cumplida**: la capacidad quedó registrada como
`agenda.que_estoy_haciendo`, sin IA.


### 0.bw «YA MANDÉ EL MAIL» — la tarjeta que no miraba para atrás (2026-08-22)

> *«En lo de SALUD también queda poco claro: “Comitentes activos sin nivel 1 —
> ya hay 1 propuesta esperando tu OK — IR A ARREGLARLO”. **Yo antes ya le mandé
> el mail pero no me dice AVISADO A LA PERSONA, y además no detecta bien qué
> avisó y qué no.** ¿Me seguís a lo que voy? No es claro.»* — user

La lente «esto lo sé hacer» de SALUD ofrecía *avisarle a la persona que se
encarga* **con el aviso ya esperando en la bandeja de esa persona**. No estaba
rota: **nunca miraba para atrás**. Es el modo de falla de siempre — nada falla,
la pantalla contesta con seguridad usando media verdad.

Y sin esa mirada las dos salidas posibles son igual de malas: o se manda de
nuevo exactamente lo mismo (*un mensaje repetido informa MENOS*, que es la razón
por la que `enviar()` desduplica), o no se manda nunca por las dudas.

**Lo que se agregó** es memoria, no un registro nuevo:
`av_agent_mensajes.enviados_sobre("control:<id>")` lee **la misma tabla que la
campanita del destinatario** (`mercado.av_agent_avisos`, por `ticker`). Un
registro aparte de «qué avisé» podría contradecir lo que el otro efectivamente
tiene — el patrón de la REGLA #9(B).

Con eso, la tarjeta cambia de estado y no solo de texto:

| situación | título | cuerpo | botón |
|---|---|---|---|
| nunca se avisó | «Esto lo sé hacer · N caso/s» | la propuesta | **qué proponés** |
| aviso abierto | «Ya avisado · esperando a N persona/s» | a quién y desde cuándo | *volver a avisar* (secundario) |
| avisado y cerrado, control sigue rojo | «Esto lo sé hacer» | «lo dieron por cerrado y sigue marcando casos» | **qué proponés** |

Y **«hay N propuesta/s esperando tu OK» dejó de mostrarse cuando hay un aviso
abierto**: con el mensaje ya mandado, esa frase le pedía al user una decisión
sobre algo que él mismo ya había hecho. Esa era, literalmente, la confusión.


### 0.bx LA CADENA NO TENÍA CICLO: 20 pasos y el problema en el medio (2026-08-22)

> *«Es demasiado complicado entender qué es lo que pasa, es como que **no tiene
> un CICLO** este control. Son un montón de pasos que si bien sirven, pero es
> como que uno debería dar paso a otro y en todo caso **que quede marcado dónde
> quedó trabado**. Pero tampoco a su vez que el user vea absolutamente todo —
> o sea, está bien que algunas cosas sirvan de ejemplo para estudiar y aprender,
> pero **yo que lo estoy entrenando quiero ver más fácil el problema**.»* — user,
> mirando el diagnóstico de BPOD7

#### Lo que mostraba, y por qué era ilegible

BPOD7 abría con **«✘ BLOQUEADO — 1 paso/s lo bloquean»** y veinte renglones
abajo. El paso 15 decía *«con los datos de hoy no se detecta nada roto»* y el
19, el que bloqueaba, decía *«el bono de hoy YA coincide con 1816: no hay nada
que arreglar y pisarlo sería empeorarlo»*.

O sea: **lo primero que se lee y lo que de verdad pasa, al revés.** El bono está
sano (TEA 3,62% contra 3,62% de 1816, 0 bps; duration idéntica) y el hallazgo
`sin_tea_con_precio` quedó viejo — su propia premisa («el XIRR no converge») la
desmiente el paso 10, que muestra la TEA calculada.

**El problema no era la cantidad de pasos.** Era que cuatro naturalezas
distintas se dibujaban iguales:

| capa | qué es | ¿puede trabar? |
|---|---|---|
| `prueba` | mide algo y puede no pasar — **es lo que decide** | sí |
| `contexto` | describe estado, no juzga | no |
| `aprender` | una lección o un caso ANTERIOR; no habla de este bono | no |
| `veredicto` | la conclusión | — |

Con las cuatro al mismo peso, encontrar el problema pedía leer las veinte.

#### La capa se DERIVA, no se lista

`estado == info` → `contexto`; cualquier otro estado → `prueba` (si puede
fallar, decide). Solo declaran capa las dos que ninguna regla puede adivinar:
una lección es `info` igual que un contexto, y eso lo sabe únicamente quien la
crea. Sin la derivación, cada paso nuevo nacería sin capa y volvería a caer en
la bolsa común — que es cómo se llegó acá.

#### «BLOQUEA» tenía DOS significados opuestos

El mismo booleano decía dos cosas contrarias:

    probé que está MAL   → hay algo que arreglar
    probé que está BIEN  → no hay nada que arreglar, y pisarlo lo empeoraría

y las dos salían con la misma ✘ roja. Ahora el paso lleva `nada_que_hacer`, el
veredicto deja de anunciar *«1 paso lo bloquea»* (sigue sin poder escribirse, y
está bien) y dice **«se comprobó que el bono está bien: este hallazgo quedó
viejo»**. Congelado por test.

#### `desenlace`: la otra pregunta

El veredicto contestaba *«¿puedo apretar APLICAR?»* — la pregunta del que va a
escribir. La que uno se hace primero es **«¿qué le pasa a este bono?»**, y
responder la primera cuando te preguntan la segunda es exactamente lo que hacía
ilegible la pantalla. `_desenlace()` la contesta en una línea, con cinco
salidas:

    roto     una prueba dice que está mal → eso es el problema
    no_se    no se pudo verificar → **no se sabe**, que no es «está bien»
    viejo    se probó que está BIEN: el hallazgo ya no aplica
    mirar    nada probado mal, pero algo no cierra
    listo    la cadena cierra entera

y con **`traba`**: la PRIMERA prueba que no pasó, en el orden en que se
corrieron. Las demás pueden ser consecuencia de ésa. Va adentro del veredicto y
no en un objeto aparte, porque salen de los mismos pasos y separarlos daría dos
cosas que pueden contradecirse — el error que este agente ya se comió tres
veces.

⚠️ Los pasos **no** son gates secuenciales que cortan: todos corren. Por eso la
pantalla dice *«acá se trabó»* y nunca *«no llegué a mirar el resto»*, que sería
falso.

#### En pantalla

1. **Una línea con el desenlace**, arriba de todo, con su color. `viejo` es
   VERDE aunque venga de un paso que bloquea.
2. **El paso donde se trabó, abierto**, sin desplegar nada. Es lo único visible
   por default: literalmente lo que pidió ver primero.
3. La conclusión del diagnóstico local.
4. **Tres solapas plegadas** — `los pasos` · `contexto` · `para aprender` — una
   por vez, como el menú de SKILLS. Nada se esconde: deja de competir por el
   lugar. Adentro de la lista, la traba va marcada otra vez («← acá se trabó»):
   ver la cadena entera y tener que volver arriba a recordar cuál era el paso
   malo es la mitad del trabajo que esto vino a sacar.

De yapa, los `**` del backend se renderizan en negrita. Venían crudos: la
pantalla mostraba `= **100.00** en 3 cupón/es`. El backend marca lo importante
desde siempre y el front lo tiraba como texto plano, así que el énfasis sumaba
ruido en vez de sacarlo. Sin `dangerouslySetInnerHTML` — se parte por `**` y los
tramos impares van en negrita, así nada de lo que llega puede volverse markup
(parte de ese texto son errores y nombres de instrumento).

#### Lo que queda pendiente y NO se tocó

**Hay dos sistemas de lentes midiendo lo mismo.** En la cadena de BPOD7, los
pasos 2 y 7 dicen los dos *«Σ de las amortizaciones futuras = 100.00 → base
100»*, y el 3 y el 9 los dos *«paridad = 102,85/100 = 102,85%, en rango»*. Son
`_diagnostico_local` y el juego de lentes de `analizar()` corriendo en paralelo
y publicando ambos. **4 de 20 renglones eran el mismo hecho dicho dos veces.**

Plegarlos lo hace tolerable, no lo arregla: es REGLA #9(B) — dos copias sin
árbitro. Mientras coincidan no pasa nada, y el día que discrepen la pantalla va
a mostrar las dos, con la misma cara. Unificarlas es el paso siguiente.


### 0.by «LOS BONOS SÍ ESTÁN» — se medía una cosa y se afirmaba otra (2026-08-22)

> *«El problema es que no está en tenencias… ¿qué tiene que ver la paridad y la
> valuación? Si es solamente meter un asset en un lugar donde hoy no figura.
> **Además LO VEO EN TENENCIAS (AuM), los dos están.** Claramente está bugueada
> esta feature porque los bonos SÍ están. Justamente no tiene nada que ver con
> 1816 agregar algo en AuM. Pero me encantaría entender a nivel código qué tiene
> que hacer eso supuestamente.»* — user, sobre PLC5O y S13N6

Tenía razón, y eran **dos bugs distintos** en la misma fila.

#### (1) El texto afirmaba algo falso

El hallazgo decía *«no entra al AuM ni a Portfolios»*. Verificado en el código,
no supuesto:

| quién | por qué campo joinea |
|---|---|
| AuM | sale de `portafolio.tenencia` — fuente única, no pasa por assets |
| Portfolios | `portfolio_sql`: `JOIN portafolio.assets a ON a.unidad = v.unidad` |
| **el detector** | `SELECT DISTINCT upper(btrim(ticker)) FROM portafolio.assets` |

**Son dos columnas distintas de la misma tabla.** Un bono puede tener su ficha
—y verse perfecto en AuM, que es exactamente lo que él ve— con `assets.ticker`
vacío o escrito distinto. REGLA #9 otra vez: *se mide una cosa y se afirma
otra*, y no falla nada — la pantalla contesta con seguridad usando el dato
equivocado y manda a buscar un problema que no es el que hay.

Lo que `assets.ticker` **sí** gobierna es el otro join, el de la cadena del AuM
(`mercado.curvas.ticker → assets.ticker → unidad`): atribuir la tenencia a su
CURVA. Sin eso el bono queda afuera de flujos, acreencias y las vistas de renta
fija. Grave, pero no lo que decía.

#### (2) El botón corría la cadena equivocada

`sin_espejo_en_assets` se emite con tipo `tasa_sospechosa` —porque el detector de
tasas es el que lo encuentra— y la acción salía del TIPO. Así, DIAGNOSTICAR
abría el arreglo de curvas: **veinte pasos de 1816, paridad, cronograma y XIRR
sobre un bono cuyo problema es una fila de catálogo.**

Es literalmente el bug de los BOPREALes (`ACCION_POR_REGLA`), que ya nos costó 17
votos: *un botón que no arregla el problema de esa fila es peor que no tenerlo*.
El mecanismo para evitarlo existía desde entonces; a esta regla nadie se lo había
puesto.

#### La cadena propia: `api/services/av_agent_espejo.py`

Cinco pasos, **cero red y cero créditos** — 1816 no tiene nada que ver:

1. ¿La casa lo tiene? (si no, el hallazgo no debería existir → `nada_que_hacer`)
2. **Qué mira el detector**: el predicado exacto, `assets.ticker = 'X'`, con su
   resultado. Era lo que prendía la fila y no se podía ver desde ninguna pantalla.
3. ¿Existe la ficha, aunque el ticker no coincida? (por `unidad`)
4. Qué se rompe **de verdad** — acá vivía la afirmación falsa
5. La causa y el arreglo

Tres causas que se arreglan distinto y hasta hoy se veían iguales:

    sin_fila     no hay ficha → falta el alta
    sin_ticker   la ficha EXISTE y `ticker` está vacío → falta UN campo
    otro_ticker  la ficha existe con otra grafía → el join no los encuentra

**`sin_ticker` es la que más importa distinguir**: mandar a dar de alta un título
que ya está dado de alta crea un duplicado.

Para medir cuál de las tres es en prod: `python -m scripts.diag_espejo_assets`
(sin argumentos lista los que disparan hoy con el predicado exacto del detector).

#### Lo que quedó como ley

El test que exigía `ACCION_POR_REGLA.values() == {"apuntar"}` ahora **deriva**:
un modo vale si lo implementa una acción que escribe (`av_agent_hacer.ACCIONES`)
o una cadena de solo lectura (`api/services/av_agent_<modo>.py` con
`diagnosticar()`). Enumerar los modos válidos a mano habría hecho que el tercero
naciera sin cobertura.


### 0.bz AHORA NO MOSTRABA LOS MOTORES (2026-08-22)

> *«**GRAVÍSIMO**: ¿que estas alertas no estén en el AHORA?? ¿Cómo no me va a
> avisar justo de los motores en el AHORA? Además sin información, sin
> contexto… si tenemos los logs tenemos los datos. **No me está avisando
> nada.**»* — user, con 5 motores en ENCONTRÓ y AHORA sin ninguno

#### La novedad es el peor criterio para la infraestructura

AHORA mostraba **solo las novedades del día**: apareció · volvió · se arregló. Un
motor que se rompe hoy entra; uno roto desde hace tres días **no**, porque su
`abierto_at` no es de hoy. O sea:

    cuanto MÁS tiempo lleva roto, MENOS visible es.

Al revés de lo que tiene que ser, y en la única familia que le corta el feed de
precios a la mesa. La novedad sirve para un hallazgo de catálogo, que espera; no
para lo que está corriendo ahora.

Bloque nuevo **ROTO AHORA**, primero de todo y sin filtro de día. Los tipos se
DECLARAN en `av_agent.EN_AHORA_SIEMPRE` (`motor_caido`, `motor_ruidoso`,
`proveedor_caido`) — misma ley que `DE_QUIEN`: adivinar por el nombre es cómo se
manda algo al cajón equivocado, y un tipo nuevo NO entra salvo que alguien lo
escriba, porque AHORA deja de ser AHORA si se llena. Suma a `novedades`: con un
motor caído, *«hoy no pasó nada»* es mentira.

#### Y no estaban ahí porque viven en OTRA TABLA

`mercado.av_agent_centinela` guarda lo que mira el DAEMON (precios · tasas ·
salud, ver `_CUBRE`); los motores los encuentra el cron `jobs.av_agent_live` y
quedan en `mercado.av_agent_items`. **Dos tablas para dos productores del mismo
objeto, y la pantalla leía una sola.** Ahora `estado()` trae los dos en la misma
conexión (4 queries, con un test AST que falla si alguna cae adentro de un
bucle).

#### El contexto ya venía; no se dibujaba

La evidencia de un motor trae `texto` (QUÉ PASÓ · A QUÉ AFECTA · SI SIGUE) y
`muestra` (la línea de log cruda) **desde siempre**. La fila mostraba solo el
título recortado. Ahora el detalle va debajo del motivo y el log crudo en una
línea con el resto en el `title`.

#### Dos cosas más de la misma pantalla

**El orden.** *«No está ordenado por hora, fijate el horario»* — y era cierto:
salían 05:10 p.m. · 12:32 · 12:32 · 12:32 · 01:30 p.m., en el orden de la query
(severidad y clave). Se ordena en el BACKEND (`_por_hora`) y por el **mismo
campo** que imprime la columna: dos criterios para lo mismo es cómo nacieron las
contradicciones que este agente ya se comió tres veces.

**El texto repetido.** Tres filas de `sin_tea_con_precio` escribían las mismas
dos líneas. El motivo es de la REGLA, no del bono: ocupa tres renglones para
decir una cosa y esconde lo único que cambia, que es el ticker. Se escribe una
vez y las siguientes dicen «↑ mismo motivo». Es la otra cara del punto anterior:
**mostrar lo que esta fila agrega**.


### 0.ca MEDIDO: eran DOS TYPEOS de un carácter (2026-08-22)

`scripts/diag_espejo_assets` en prod, sobre los 2 que disparan la regla:

| | `mercado.curvas.ticker` | `portafolio.assets.ticker` | la diferencia |
|---|---|---|---|
| PLC5O | `PLC5O` | `PLC50` | la **O** es un **cero** |
| S13N6 | `S13N6` | `S13B6` | la **N** es una **B** |

**Los dos son CASO C** — la ficha existe, con cartera y emisor cargados, y el
campo TICKER está tipeado mal por un carácter. No falta el alta ni falta el
campo: está **mal escrito**. Y como `portafolio.assets` es catálogo que carga la
mesa a mano, esto va a volver a pasar.

#### Por qué se puede arreglar solo, y por qué antes no se podía

Porque **el valor correcto no lo tipea nadie**: viene adentro de la propia
`unidad`, que es la PK de la fila y la escribe Aunesa —
`'[84857] PLC5O - ON PLUSPETROL…'` → `PLC5O`, que coincide exacto con la curva.
No se adivina por parecido ni por distancia de edición (eso es justo lo que
REGLA #9 A prohíbe): se lee de **la fuente que ninguna de las dos copias
escribió**.

Acción nueva `assets.ticker`, con tres guardas que son lo que la separa de un
UPDATE peligroso:

1. **El código de la unidad tiene que ser una curva existente.** Si no, no
   sabemos cuál es el bueno → no se propone.
2. **El ticker actual no puede ser el de OTRA curva real.** Si `PLC50` fuera un
   papel de verdad, pisarlo acá le rompería el join a ESE. Se reporta y no se
   toca.
3. **Una sola ficha por unidad.** Con dos, cuál es la buena es una decisión, no
   una derivación.

Las tres se **vuelven a correr al aplicar**, no solo al proponer: entre las dos
cosas pueden pasar horas y alguien pudo tocar el catálogo a mano. Escribe por
`assets_sql.set_campos` —la misma puerta que Manager— y verifica releyendo.

⚠️ `sin_fila` **no** lleva botón: dar de alta un título es cargar cartera,
emisor, clase y calificación, y nada de eso se deriva de ningún lado.

#### Y lo que faltaba de verdad: nadie los estaba comparando

El par `curvas.ticker` ↔ `assets.ticker` es **el mismo dato en dos lugares sin
árbitro** — REGLA #9(B) exacta, y nadie lo miraba. Ahora está declarado en
`core/duplicados.DUPLICADOS` (`ticker_curva_vs_assets`) con el árbitro escrito:
**gana la UNIDAD**, que no es ninguna de las dos copias.

**Sin `arreglo_sql`, a propósito.** El UPDATE «obvio» es correcto en los dos
casos medidos y peligroso en general (guarda 2). Va por la acción, que verifica
caso por caso.

Y el control diario `assets_ticker_partido` **no reimplementa el predicado**:
lo lee de `core.duplicados` vía la función nueva `una(id)` (un solo par, sin
truncar, y `{"ok": False}` cuando no pudo mirar — *el silencio no es un verde*).
Escribirlo dos veces sería exactamente el problema que ese módulo existe para
evitar, y no es hipotético: es lo que pasó con `preferencia`, escrita tres veces
y eligiendo distinto en cada una.

**El resultado**: la próxima vez que alguien tipee mal un ticker, el agente lo
canta esa misma noche y ofrece el arreglo de un click — en vez de que el bono
desaparezca en silencio de flujos y renta fija hasta que alguien mire la
pantalla correcta.

#### ⚠️ El botón habría propuesto CERO, y lo cazó un test

Antes de que el user apretara nada: **`proponer()` asumía que el sujeto del caso
era un TICKER, y el control emite la UNIDAD.** El `_sujeto()` devolvía
`'[84857] PLC5O - ON PLUSPETROL…'`, el código comparaba eso contra tickers, no
matcheaba nada y devolvía lista vacía — **sin error y sin log**. Un botón que
aparece, no hace nada y no explica por qué: exactamente la pared que esta acción
vino a sacar, reproducida adentro de la acción.

Arreglado aceptando **las dos formas** (el SQL matchea por `unidad` exacta *o*
por el código extraído de ella), porque las dos existen de verdad: el control
habla de fichas, la fila de ENCONTRÓ habla de bonos.

Y las tres guardas salieron a `_elegir_ticker()`, **pura y testeable**: una
decisión que no se puede probar sin la base es una decisión que nadie va a
probar. Los tests usan los datos REALES del diag — con datos inventados habrían
pasado igual con el bug adentro.

#### El regex quedó en TRES lugares y hay un test que lo vigila

El patrón que saca el código de la unidad vive en `acreencias._RE_CODIGO`
(Python), en el SQL del duplicado y en el SQL de la acción. **No se pueden
unificar** —uno corre en Python y los otros dentro de Postgres— así que lo único
que queda es exigir que digan lo mismo, y eso es un test. Es REGLA #9(B) en
chico: si una copia se separa, el control marca un caso que la acción no propone
(o al revés) sin que nada falle.


### 0.cb «ES CLICKEAR AL PEDO» — cuatro bugs, una misma firma (2026-08-22)

> *«NO HACE NADA, es clickear al pedo, otra vez. Ya es un chiste que haya que
> hablar de esto miles de veces. Y encima que si no pudo encontrar el error algo
> tiene que hacer, listo, **DESAPARECER**. Ya no sé cómo explicar que no quiero
> basura acá… y si encima estoy marcando que me interesa escuchar estas cosas,
> ¿sigue siendo un botón que no hace nada?»* — user

Cuatro cosas distintas, todas con la misma firma: **nada falla, y lo que el user
hace no deja huella.**

#### (1) El voto se guardaba y la pantalla no se enteraba

El `✔ SIRVE` / `✖ ES RUIDO` **sí escribía** en `mercado.av_agent_evals`. Lo que
faltaba era **una línea**: `await recargar()`. El «✔ te sirve» vivía en un
`useState` del componente; al cambiar de tab React lo desmonta, al volver lee el
`data` viejo —donde `ya_votado` sigue en `false`— y dibuja los botones otra vez.

El user lo describió con precisión quirúrgica: *«me voy de ENCONTRÓ a AHORA,
vuelvo, y están los botones igual»*. Literal.

Y de yapa es lo que hace que **`✖ es ruido` SAQUE la fila**: el backend ya la
marca y la vista ya la filtra — pero solo cuando los datos se vuelven a leer.

#### (2) El informe masivo desaparecía al cambiar de tab

> *«Literal: estás en una vista, hacés algo, te vas a otra y desaparece todo.
> Estaba haciendo el diagnóstico, me pasé a AVISOS, volví y se borró.»*

No se borraba nada: el informe vive en el backend y `GET /masivo` devuelve la
última corrida. Lo que se perdía era el `useState`. **Una corrida de 4 minutos y
92 casos desaparecía de la pantalla por tocar otra solapa**, y la única forma de
recuperarla era volver a correrla. Ahora se lee al montar.

#### (3) «SIN PUERTA» era mentira en 13 de 26 — la CUARTA copia del ruteo

`av_agent_masivo` tenía su propio `if/elif` con **cuatro** modos
(`salud · flujos · alta · arreglo`) mientras `accion_de()` ya devolvía **ocho**.
Los otros cuatro caían al `else` y el informe los declaraba *«SIN PUERTA — el
agente los ve y todavía no sabe tocarlos»*.

Medido en el masivo #14: de 26 «sin puerta», **13 tenían acción desde hacía
días** — 11 `pata_equivocada` (que arregla `mercado.apuntar_pata`, escrita
justamente porque el user se hartó de verlos 17 veces) y 2
`sin_espejo_en_assets` (escrita ayer).

    El agente decía que no sabía hacer algo que sabía hacer,
    y el informe pedía construir lo que ya estaba construido.

Es el bug de los BOPREALes en su **tercera reencarnación**: una tabla de ruteo
copiada. Ahora hay UNA (`PUERTAS`) y un test exige que cubra todo lo que
`accion_de()` puede devolver. Otro test exige que **ninguna puerta escriba**: el
masivo diagnostica 92 bonos de una, y una puerta que aplicara convertiría una
corrida de rutina en 92 escrituras que nadie aprobó.

#### (4) Lo probado-sano no se cerraba

SFD34 y BPOD7 salían como `sin_tea_con_precio` y la cadena terminaba en *«se
comprobó que el bono está bien, este hallazgo quedó viejo»* — con la TEA
coincidiendo con 1816 **a 0 bps**. El agente lo PROBÓ y la fila seguía ahí.

Ahora el masivo los **cierra** y lo cuenta en una categoría propia
(`CERRADOS: se comprobó que ya no aplican`).

⚠️ **Por qué lo cierra el masivo y no el modal**: *mirar no puede escribir*. El
masivo es una pasada deliberada que ya diagnosticó todo, así que cerrar es su
conclusión y no un efecto secundario de haber abierto una pantalla. Y si no
puede cerrar, **lo dice** — un «cerrado» que no cerró nada es peor que no
intentarlo, porque la fila reaparece mañana sin explicación.

#### (5) Los títulos cortados

`control:patas_equiv…` no dice nada. El prefijo de familia (`control:` / `job:`)
existe porque **la clave lo necesita** —un job y un control pueden llamarse
igual— pero en la pantalla se come el ancho y deja el nombre cortado, y la
familia ya se lee en la columna de al lado. El backend publica `nombre` sin
prefijo (`patas equivocadas`) y la columna dejó de tener ancho fijo. El sujeto
crudo queda en el `title`: para buscarlo en la base hace falta el exacto.

#### El censo: `scripts/diag_encontro` — y la primera versión estaba mal

⚠️ **Contó 408 donde la pantalla mostraba 98.** Consultaba
`mercado.av_agent_items` directo —el objeto canónico— y esa tabla guarda TAMBIÉN
los avisos dirigidos (**142 `saldos_comitentes`**, que son mensajes a operadores
y no problemas) y los sensores. La pantalla, en cambio, lee la foto de la última
corrida de `mercado.av_agent_hallazgos`.

Y clasificó **406 de 408 como «falta escribir el arreglo»** cuando las cinco
pilas más grandes ya tenían acción: armó su propio conjunto de reglas-con-acción
y se le escaparon las que se resuelven **por CONTROL** (`POR_CONTROL`), que son
justamente las grandes — 133 `patas_dolar_sin_pedir` salieron como deuda
teniendo `mercado.pata_dolar` escrita.

    Un diag que contradice a la pantalla que viene a explicar no sirve para
    decidir nada — y los dos errores son el mismo: **reimplementar en vez de
    derivar**, que es lo que este archivo viene señalando en todos lados.

Corregido: llama a `av_agent_vista.vista()`, que es literalmente lo que el modal
dibuja, y cada fila llega con `accion` ya resuelta por `accion_de()`. También
imprime cuántos abiertos tiene `av_agent_items` por tipo: si el número no se
dice, el día que alguien mire esa tabla va a ver 400 y va a pensar que la
pantalla esconde cosas.

#### Aplicar en lote: `scripts/agente_aplicar`

Las pilas grandes no necesitaban que se escribiera el arreglo — necesitaban
poder aplicarlo sin un click por caso:

| control | casos | acción |
|---|---|---|
| `patas_dolar_sin_pedir` | 133 | `mercado.pata_dolar` |
| `assets_sin_cartera` | 24 | `assets.cartera` |
| `fci_incompletos` | 8 | `assets.fci` |
| `patas_equivocadas` | 6 | `mercado.apuntar_pata` |
| `comitentes_sin_nivel1` | 5 | `avisar.responsable` |

**No es un camino nuevo de escritura**: llama a `hacer.proponer()` y
`hacer.aplicar()`, las mismas del modal — misma propuesta, misma verificación
releyendo, mismo libro. **Dry-run por default** e imprime, por propuesta, de qué
a qué.

Y **el riesgo de cada acción se DECLARA** (`_RIESGO`), con un test que exige que
ninguna quede sin declarar. No son igual de reversibles: pedir una pata no toca
ninguna valuación, pero `assets.cartera` **decide el divisor del AuM** — o sea
que escribe plata. Adivinarlo del nombre sería soltar 24 escrituras creyendo que
no se toca nada. Por eso también existe `--tope`: probar con 5 y mirar antes de
soltar 133 es el orden correcto, no una precaución opcional.

#### El censo: `scripts/diag_encontro`

Para poder vaciar ENCONTRÓ hay que contestar algo que la pantalla no contesta:
**de los N abiertos, ¿cuántos son de cada clase?** Las cinco clases no son
severidad — son **qué trabajo hace falta**, que es lo único que decide el orden:

    AUTO_CERRABLE      el agente ya probó que no aplica → dejar de mostrarlo
    RUIDO_ESTRUCTURAL  no puede dejar de aparecer nunca → se arregla el DETECTOR
    TIENE_PUERTA       hay acción; si sale «sin puerta» es un bug de RUTEO
    FALTA_ACCION       deuda real, **ordenada por volumen**
    HUMANO             criterio de la mesa — el único resto legítimo

#### La deuda que queda medida, y por qué NO la toqué todavía

**`moneda_flujo_contradice`: 21 casos, el pico del informe.** Todos dicen lo
mismo — `moneda_flujo`=ARS y los ejes piden USD/DL — y la cadena remata *«con
`moneda_flujo` bien, el motor la resuelve solo»*. El valor correcto ya lo calcula
el propio detector (`moneda_flujo_esperada`), así que la acción es derivable.

**No se escribió en esta pasada a propósito**: ese campo decide **cómo el motor
convierte el precio**, o sea que tocarlo *cambia una valuación*. Todo lo de hoy
fue pantalla y ruteo, sin mover un número. Escribir 21 valuaciones un viernes a
la noche, sin que nadie mire el resultado hasta el lunes, es exactamente la clase
de cambio que este proyecto no hace.


### 0.cc EL CENSO REAL, y dos bugs que solo aparecieron al correr las acciones (2026-08-22)

Con el diag arreglado, los **98 de ENCONTRÓ** quedan así:

| clase | n | qué significa |
|---|---|---|
| **TIENE PUERTA** | **79** | hay un botón escrito |
| RUIDO ESTRUCTURAL | 10 | el detector no puede dejar de gritarlos |
| HUMANO | 8 | motores y proveedores: se miran |
| FALTA ACCIÓN | **1** | deuda de código de verdad |
| AUTO-CERRABLE | 0 | — |

⚠️ **«Tiene puerta» NO quiere decir «se puede aplicar hoy».** Quiere decir que
existe un botón; si la cadena lo bloquea es otra pregunta, y esa la contesta el
masivo (en el #14: 49 bloqueados contra 4 listos). Son dos cosas distintas y
mezclarlas sería exactamente el error que este doc viene corrigiendo.

**La deuda de código real es UNA.** No 406, como decía la primera versión del
diag: `moneda_flujo_contradice` es la única regla grande sin arreglo escrito, y
sigue esperando el OK del user porque **escribe una valuación**.

#### Los 10 de ruido estructural son un DETECTOR, no 10 problemas

7 `sin_escribir` sobre tablas que por diseño no se escriben solas
(`realtime.schema_migrations` es interna de Supabase) + 3 `tabla_nueva`, que
informa **una vez** y queda abierta para siempre. Se arreglan en el detector.

#### Correr la acción encontró lo que la pantalla nunca iba a encontrar

**`assets.fci` estaba ROTA y nadie lo sabía.** `assets_rows` proyecta con claves
UPPERCASE y la acción las pedía en minúscula: `KeyError: 'cartera'` adentro de
una comprehension. La acción entera explotaba y el informe decía «no pude
proponer» sin más.

Y **el test la cubría… con un mock que mentía**: devolvía `emisor` en minúscula,
o sea que afirmaba que la acción andaba **contra un contrato que no existe**.

    Un mock con la forma equivocada no es media garantía: es CERO garantía, y
    encima TAPA el bug.

Tres arreglos, no uno: la llamada, el mock, y **`assets_rows` valida en el
borde** — un campo desconocido ahora dice qué claves hay en vez de reventar seis
frames más abajo. Más un test que barre los tests buscando mocks de esa función
con claves en minúscula: cazó **otros dos** en el mismo archivo, uno de los
cuales hacía pasar a `test_si_los_hermanos_no_coinciden_no_se_propone` **por el
motivo equivocado** (no proponía porque el mock estaba vacío, no porque los
hermanos se contradijeran).

**Esto solo se descubre EJECUTANDO.** Esa acción no se aprieta desde la pantalla,
así que llevaba rota vaya a saber cuánto — y `scripts/agente_aplicar` la corrió
por primera vez.

#### El duplicado que declaré ayer gritaba 303 veces por 2 problemas

`ticker_curva_vs_assets` comparaba `assets.ticker` contra el código de la unidad
**para TODOS los assets**. Pero ese código solo ES un ticker cuando el asset es
un bono: en un FCI la unidad dice `[2598] cafc1103- 2598 - IEB Renta Fija` y en
un OTC `[OTC - DLR052027]`. **301 de 303 no significaban nada.**

Un chequeo que grita 303 veces por 2 problemas reales enseña a ignorarlo — que
es el daño exacto que `core/duplicados` existe para evitar. Ahora el SQL joinea
contra `mercado.curvas`: la misma guarda 1 que ya tenía la acción. Si el código
de la unidad no es una curva, no sabemos cuál de los dos nombres es el bueno, así
que **no hay divergencia que declarar**.

### 0.ci EL COTEJO GENERAL, y una acción que creó 6 anomalías (2026-08-22)

Se aplicaron los 6 `pata_equivocada` (BPOA7/8, BPOB7/8, BPOC7, GD46), el control
`patas_equivocadas` quedó en **0** y ENCONTRÓ siguió mostrando **17**. Tercera
vez en la misma sesión con la misma forma: **control verde, hallazgo vivo**.

Las dos anteriores se taparon de a una regla por vez. **Así se llega a la
cuarta.** Ahora hay un cotejo general: *un hallazgo cuya regla espeja un control
caduca cuando ese control queda en cero*. Y la relación regla → control **ya
existía y no hubo que escribirla** — cada `Accion` declara `causa` (la regla) y
`sobre` (el control), así que una acción nueva trae su cotejo puesto.

⚠️ **Solo cuenta el control en CERO, a propósito.** Con casos activos se podría
matchear sujeto por sujeto, pero las claves no son el mismo string
(`assets_ticker_partido` guarda la UNIDAD y el hallazgo habla del TICKER) y un
match fallido se leería como «resuelto». Cero activos no tiene esa ambigüedad.
`pata_equivocada` sale de la lista de deuda del test; quedan dos.

#### Y la acción creó 6 anomalías nuevas: faltó reiniciar el OTRO motor

Junto al verde apareció `Renta fija cotizando sin TEA/TNA: 16 (▲6 nuevos)` — y
los 6 nuevos son **exactamente** los 6 que se acababan de apuntar.

La causa es la trampa que este mismo doc describe: **son DOS motores.**
`motor_rofex` PIDE el precio y `motor_curvas` CALCULA la TEA, y **los dos arman
su universo al arrancar**. Se reinició solo el primero, así que el bono quedó con
precio de la pata D y la TEA escrita bajo el símbolo viejo. El bono no está roto:
está partido entre un motor nuevo y uno viejo.

**Regla que queda: una acción que cambia `mercado.curvas.instrumento` toca a los
DOS motores, y el que la aplica tiene que decir los dos reinicios.** Decir uno es
peor que no decir ninguno — deja el sistema en un estado mixto que ningún
detector distingue de un bono realmente sin tasa.

### 0.ch «NO CAMBIA CASI NADA»: el censo decía botón donde no había botón (2026-08-22)

Tres rondas de trabajo y ENCONTRÓ pasó de **98 a 95**. El user, textual: *«te
juro que ya estoy perdido… no cambia casi nada»*. Tenía razón, y la culpa no era
del sistema: **se estuvo arreglando la plomería (contadores, cotejos, controles)
en vez de aplicar los arreglos**, porque el censo estaba señalando el lugar
equivocado.

#### Lo medido

Hay **9 acciones registradas** en `av_agent_hacer.ACCIONES`. De los 77 hallazgos
que el censo clasificaba como *«TIENE_PUERTA → apretar el botón»*:

| regla | n | ¿acción de lote? |
|---|---|---|
| `pata_equivocada` | 17 | ✔ `mercado.apuntar_pata` |
| `salud_control` / `salud_job` | 13 | ✔ parcial, vía `POR_CONTROL` |
| `moneda_flujo_contradice` | 21 | ✖ modo `arreglo` |
| `sin_ejes` | 9 | ✖ modo `arreglo` |
| `paridad_fuera_de_rango` | 8 | ✖ modo `arreglo` |
| `sin_tea_con_precio` | 7 | ✖ modo `arreglo` |
| `tea_fuera_de_rango` | 2 | ✖ modo `arreglo` |

**47 de 77 no tienen arreglo de lote escrito.** `"arreglo"` NO es una acción: es
el **modo del panel con IA**, que se usa caso por caso mirando el bono. El censo
mandaba a correr `agente_aplicar` sobre 47 casos que ese script no puede tocar
—solo conoce las 9 de `ACCIONES`— y el comando salía sin errores y sin efecto.

#### La causa: tres vocabularios para la misma pregunta

  · **`accion_de()`** devuelve un **MODO de pantalla** (`arreglo`, `alta`,
    `flujos`, `apuntar`, `espejo`, `salud`…).
  · **`ACCIONES`** tiene los **ARREGLOS EJECUTABLES**, con `proponer` /
    `aplicar` / `verificar`.
  · El **censo** trataba al primero como si fuera el segundo.

Es exactamente el defecto que esta sesión viene persiguiendo —dos definiciones
de lo mismo que no se hablan— y esta vez el costo no fue un dato mal contado
sino **tres rondas de trabajo apuntadas al lugar equivocado**.

#### El arreglo

El censo parte `TIENE_PUERTA` en dos pilas que responden preguntas distintas:

  · **`APLICABLE_EN_LOTE`** — hay una `Accion` registrada que lo cubre, y el
    censo imprime **el comando exacto** (`agente_aplicar --accion <id>`).
  · **`UNO_POR_UNO`** — hay modo de pantalla pero no arreglo de lote: se abre en
    el modal de a uno, **o se escribe su acción**, que es justamente lo que lo
    volvería masivo.

Se **deriva del registro real** (`ACCIONES` + `POR_CONTROL`), no de una lista a
mano: el día que alguien escriba la acción de `sin_ejes`, esos 9 se mueven de
pila solos.

#### Y la otra mitad: `Accion` NO era el único mecanismo de lote

Al separar las pilas quedó que **53 hallazgos eran «uno por uno»**. Falso, y por
poco se manda ese mapa: el modo `arreglo` tiene **su propio par simular/aplicar**
(`av_agent_alta.simular_arreglo` / `aplicar_arreglo`) —lo que aprieta el botón de
la pantalla— y encadenarlo en lote es perfectamente legítimo. `agente_aplicar`
solo conocía `ACCIONES` y por eso no los veía.

Lo que hace que el lote sea SEGURO ya estaba escrito: **`aplicar_arreglo` vuelve
a simular adentro** y se niega si `puede_aplicar` es falso, con la guarda en un
solo lado. El script no re-decide nada — un gate propio acá sería el cuarto
criterio contradiciéndose con los otros tres.

Es la única puerta que **PISA un dato existente** y toca ejes y moneda (errarle a
`moneda_eje` es plata mal contada), así que el dry-run no muestra «se puede»:
muestra **de qué a qué** cambia cada eje, y **los trabados se imprimen** — un
lote que dice «apliqué 4» y calla los otros 41 es el mismo silencio que hizo
perder tres rondas.

La lista de modos aplicables vive **una sola vez**, en el script que los ejecuta,
y el censo la importa de ahí. Copiarla habría vuelto a mandar a correr un comando
que no hace nada.

#### El primer dry-run salió inservible, y las dos fallas eran de PRESENTACIÓN

`--accion arreglo` sobre 42 casos: **4 listos, 38 trabados**. Y no se podía
decidir nada con esa salida:

  · los 4 aplicables mostraban **`→ —`**. Sus ejes ya estaban bien y lo que el
    arreglo corrige es **otra cosa** (la escala del cuadro, el CER de emisión),
    que el dry-run no imprimía. Una fila vacía justo donde SÍ se va a escribir
    invita a aplicar a ciegas — en la única puerta que pisa datos existentes.
  · los 38 trabados mostraban el **título** del chequeo: *«BVCVO: La métrica
    vuelve al rango»*, que **se lee como que pasó**. El título dice qué se
    EXIGE; el `detalle` dice qué se ENCONTRÓ. Poner el requisito en el lugar del
    motivo deja al que mira sin nada.

Arreglado: el motivo sale del `detalle`, y los trabados se **agrupan**
normalizando los números (`TEA 41,2%` y `TEA 38,9%` son la misma traba). Veinte
bonos esperando lo mismo es UN problema; contados de a uno parecen veinte.

**El dato que importa igual: el pre-flight está haciendo su trabajo.** 38 de 42
se traban por falta de un insumo real (1816, un precio, un CER), no por un bug.
`arreglo` no es un botón masivo — es un botón por caso con una cola larga de
casos que todavía no tienen con qué resolverse.

#### El hallazgo grande: el DETECTOR juzga por DEFECTO y el ARREGLO por SÍNTOMA

Con la salida ya legible, el desglose de los 38 trabados cuenta algo que ninguna
pantalla decía. El gate del arreglo es, literal:

```python
OK if (en_rango and estaba_mal) else BLOQUEA
```

  · `estaba_mal` → la paridad de HOY estaba fuera de rango;
  · `en_rango`   → después del arreglo vuelve adentro.

O sea que **el arreglo solo se habilita si el SÍNTOMA es visible en la métrica**.
Y `moneda_flujo_contradice` fue extendido, a propósito, para cazar **el DEFECTO
sin el síntoma** — está escrito en su propio comentario: *«30 de 140 bonos tienen
`moneda_flujo` contradiciendo a sus ejes y solo 8 habían disparado algún
hallazgo; los otros 22 están igual de mal valuados y no aparecían en ninguna
pantalla»*.

**Las dos mitades se construyeron con criterios opuestos y no pueden ponerse de
acuerdo nunca para esta clase.** Medido en el dry-run de los 42:

| traba | n | qué significa |
|---|---|---|
| «lo de hoy YA estaba en rango» | 9 | el defecto es real y la métrica no lo puede juzgar |
| paridad `— → —` (sin valor) | 12 | ni siquiera hay métrica con qué juzgar |
| 1816 no tiene el bono | 9 | `sin_ejes` **no** se puede resolver desde 1816 |
| nuestra paridad vs la de 1816 | 3+2 | discrepancia real, hay que mirarla |
| falta CER de emisión | 2 | insumo que no tenemos |
| sin precio | 1 | nada con qué cotejar |

Es REGLA #9 en otro nivel: no son dos copias de un dato, son **dos definiciones
de «está mejor»**. Y el costo es que 21 defectos de integridad reales quedan sin
puerta, con un botón que los mira y siempre dice que no.

**No se cambió el gate.** Para un defecto de auto-contradicción «mejor» no se
mide con la paridad, se mide con *«la contradicción desapareció»* — y eso es un
segundo criterio de aceptación que escribe valuaciones. Es una decisión de
diseño, no una derivación, y va con el user a la vista.

#### Y una de performance, de yapa

`simular_arreglo` levanta un `MotorCurvas` **por bono**: 42 bonos recargaron
«Días hábiles» ~60 veces y el CER ~10, tardaron **4,5 minutos** y taparon la
salida entera con su propio log. El log se calla en el lote (sube el nivel de
esos loggers, sin tocar el motor: es ruido solo en ESTE contexto). La recarga
por bono queda anotada como deuda.

#### La regla que queda

**Una lista de trabajo tiene que decir CON QUÉ se hace cada cosa, no solo que se
puede hacer.** «Tiene puerta» sin decir cuál es la puerta manda a buscarla, y si
esa puerta no existe la búsqueda no termina nunca — que es literalmente lo que
pasó tres veces seguidas.

### 0.cg EL TIPO NO DECIDE SI ALGO SE PUEDE REVERIFICAR — LA REGLA SÍ (2026-08-22)

Se corrigieron los dos tickers (`PLC50→PLC5O`, `S13B6→S13N6`), el control
`assets_ticker_partido` quedó en **0**… y **PLC5O y S13N6 seguían en ENCONTRÓ**.
Dos partes del sistema afirmando lo contrario en la misma pantalla.

Es el mismo patrón por **quinta** vez (TZXM8, BADLAR, IGNORAR, DICP y ahora
esto), pero con una variante nueva y por eso se escapó: el cotejo no faltaba por
olvido ni estaba descartado por escrito — **estaba descartado para el TIPO
equivocado**.

`_caduco` decidía «¿esto se puede reverificar barato?» mirando el **tipo**, y
`sin_espejo_en_assets` viaja bajo `tasa_sospechosa`. El comentario decía, con
razón, que una tasa depende del precio del día y no se puede reverificar sin
volver a cotejar contra 1816. Cierto para `paridad_fuera_de_rango`,
`sin_tea_con_precio` y `tea_fuera_de_rango`. **Falso para
`sin_espejo_en_assets`**, que no es una tasa: es *«¿existe este ticker en
`portafolio.assets`?»*, y eso es UNA query.

El tipo agrupa por **de dónde salió** el hallazgo; lo que decide si se puede
reverificar es **qué afirma**. Usar el primero como proxy del segundo funciona
hasta que un detector emite dos clases de afirmación, y ahí falla en silencio.

#### Cómo se arregló, y lo que se cuidó de no romper

La query de los tickers con ficha vivía **inline dentro de `relevar()`**.
Copiarla al cotejo habría creado la divergencia de siempre —la pantalla podría
afirmar que falta la ficha con un criterio y que sobra con el otro— así que se
extrajo a **`av_agent.tickers_con_ficha()`** y la usan los dos. `None` sigue
significando «no pude mirar» y **no caduca nada**: un huérfano que desaparece
porque se cayó una query es la mentira más cara que puede decir esto.

Y el test que protegía este archivo **hizo su trabajo dos veces**: cazó el
cambio de semántica y además que se había agregado **un viaje más a Supabase por
lectura de pantalla**. Ese peaje es real (~8,5 ms de pura distancia, y la
pantalla se abre muchas veces por día), así que la lectura va por un wrapper
`@cached(ttl=45)` en la vista mientras el detector —que corre una vez por
noche— sigue leyendo fresco.

#### La deuda queda NOMBRADA, no olvidada

Tres reglas más son hechos de base y todavía no tienen cotejo en la lectura:
**`pata_equivocada`** (17), **`sin_ejes`** (9) y **`moneda_flujo_contradice`**
(21). Si se arreglan hoy, van a quedarse en pantalla igual que estos dos.

Están listadas en `test_av_agent_cotejo.py` con un `assert` sobre el largo de la
lista: **sacar una obliga a editar el test, y sumar una también.** Es la
diferencia entre una decisión y un olvido — que es exactamente lo que falló acá.

### 0.cf EL CONTROL DECÍA 133 Y EL BOTÓN ARREGLÓ 16 (2026-08-22)

Con el contador ya arreglado (§0.ce), la primera corrida de verdad destapó algo
peor: `patas_dolar_sin_pedir` cantaba **133 casos**, se apretó el botón y se
arreglaron **16**. Los otros 117 contestaron, cada uno, *«ya la escuchamos y
tiene precio: no hay nada que hacer»*.

El botón estaba bien. **El control estaba contando cosas que la acción no
toca.**

#### La misma pregunta, contestada en dos lugares

*¿Alguien pide la pata en dólares de este bono?* vivía escrita dos veces, y las
dos versiones diferían en dos cosas:

| | el CONTROL (`_chk_patas_dolar_sin_pedir`) | la ACCIÓN (`av_agent_pata.explicar`) |
|---|---|---|
| **qué pata** | `ORDER BY e.simbolo` → alfabético → el **CABLE** (`BPA7C` < `BPA7D`) | `core.especies.mejor` → **MEP** sobre cable, 24hs sobre CI |
| **«nadie la pide»** | `adhoc_subscriptions IS NULL` | adhoc **∪** estar en `market_snapshot` |

O sea que **miraban dos símbolos distintos del mismo bono**. El control evaluaba
el cable —que efectivamente no tiene precio— y el botón iba a pedir el MEP, que
sí lo tenía. Los dos eran internamente coherentes y los dos contestaban con
seguridad. Nada falló.

**Y la elección alfabética ya se había arreglado DOS veces.** Está escrito
textual en `_elegir`: *«acá había una copia y en el diag había otra —un
`sorted()` alfabético— y por eso los dos elegían cable»*. Este control era el
**tercer** lugar, y nadie lo tocó porque nada fallaba: el número simplemente
estaba inflado, y un número inflado no se queja.

Lo segundo es el error del AO29 otra vez (§0.v): el `LEFT JOIN` no distinguía
«no está en el snapshot» de «está con precio 0». Son tres estados, no dos, y el
motor puede estar suscribiendo la pata desde el master o desde el universo de
portfolio **sin ningún adhoc**.

#### El arreglo: el control le pregunta al botón

El SQL pasa a ser una **preselección** y el veredicto lo da `explicar()` — la
misma función que corre al apretar el botón. Se queda solo con
`hay_que_pedirla`, que es el único veredicto con trabajo; `con_precio`,
`escuchada_sin_punta` y `cerrado_sin_saber` son estados legítimos sin botón. El
símbolo que reporta la fila también sale de ahí (`pedible`): si la fila nombrara
uno y el botón pidiera otro, no habría forma de verificar lo que uno hizo.

Costo **medido, no estimado**: la corrida llamó a `explicar()` 133 veces en
`aplicar` y terminó sin problema; Primary está cacheado con TTL, así que son ~4
queries por bono. Es un cron nocturno.

#### Medido después de aplicarlo: 133 → **0**

No los ~13 que se habían anticipado, y los números cierran exacto: **117 ya
tenían precio** en la pata correcta (el bug de la elección alfabética) y **16**
son las que se pidieron en esa corrida, que al quedar suscriptas dejan de tener
trabajo. `TIENE_PUERTA` pasó de **252 casos a 123**.

#### Y por eso mismo el verde ahora se audita

**Un control que pasa de 133 a 0 tiene exactamente la misma forma que un
detector que se quedó ciego.** Los dos son un tilde verde y desde la pantalla no
se distinguen. Es la regla de §0.s aplicada acá: *si el chequeo no pudo mirar,
lo tiene que decir — el silencio se lee como un verde*.

El control loguea el desglose por veredicto (`0 con trabajo, de 133 evaluados ·
con_precio=117 · escuchada_sin_punta=16`), así el cero queda auditable sin
volver a consultar nada. Y si algún día no hubiera **ni un candidato** —un
`WHERE` que dejó de matchear, una columna renombrada— eso sale como `warning`
explícito: cero candidatos produce cero hallazgos con el mismo verde que «está
todo bien», y es el único caso en que el control no está diciendo nada.

#### La regla que queda

**Un control tiene que contar lo que su acción puede arreglar.** Si cuenta más,
el número no mide trabajo: mide una consulta que nadie va a ejecutar. Y la forma
de garantizarlo no es escribir bien el mismo criterio en los dos lados —eso ya
se intentó tres veces con este mismo criterio— sino que **haya un solo lado**:
el control deriva de la acción, o la acción del control, pero no coexisten dos
definiciones. Congelado en `tests/unit/test_control_pata_dolar.py`.

### 0.ce EL AVANCE PARCIAL ERA INVISIBLE: se contaban CONTROLES, no CASOS (2026-08-22)

Continuación directa de §0.cd, y la parte que ese diagnóstico **no** alcanzó.
Después de aplicar el cotejo vivo de SALUD, el censo volvió **idéntico por
tercera vez**: 98 hallazgos, dígito por dígito. Mi predicción («`fci_incompletos`
baja de 8 a ~5-6») falló.

#### Lo medido, leyendo el código y no adivinando

Tres cosas, todas verificables en el fuente:

1. **`detectar_salud` SÍ emite `tipo == "salud"`** (`av_agent.py:1245`, primer
   argumento de `_hallazgo`) — el cotejo que se había shipeado no era un no-op.
2. **`salud._chequeos_controles` ya publica `n = len(activos)`** — el número de
   casos existía y nadie lo leía.
3. **Un control es UNA fila de ENCONTRÓ, con N casos adentro.** `fci_incompletos`
   tenía 8 casos: 5 sin emisor (los que se arreglaron) y 3 sin ticker (unidades
   que no matchean el formato CAFCI, que esa acción no puede tocar).

O sea: **el control siguió rojo, con toda la razón, así que su fila se quedó — y
el conteo de filas no podía moverse.** El mecanismo funcionaba perfecto y era
invisible.

#### El error de diseño: tachar estaba resuelto, reescribir no

`_salud_por_id` devolvía `{id: estado}`. Con eso el cotejo sabe contestar UNA
sola pregunta —¿está en verde?— y por lo tanto sabe hacer UNA sola cosa: tachar
la fila. Pero **el caso normal no es que algo se arregle entero: es que se
arregle una parte.** Para ese caso el estado no alcanza y el texto de la fila
quedaba congelado en el de la foto de anoche.

`_salud_por_id` ahora devuelve **el chequeo entero** y `_refrescar_salud`
reescribe motivo, severidad y conteo en la lectura. La foto guarda `n_casos`, así
que la fila puede decir **«3 casos ▼ eran 8»**. Mismo principio de siempre —*la
foto se muestra, pero nunca sin cotejarla*— aplicado al TEXTO y no solo a la
existencia de la fila.

#### Lo que además tapaba el resultado

  · **`agente_aplicar` tiraba el re-chequeo a la basura.** `hacer.aplicar()`
    vuelve a correr el control y devuelve el resultado en `recontrol`; el script
    imprimía solo «aplicadas 5/5». Ese era EL número que contestaba «¿sirvió?» y
    no se veía. Ahora se imprime.
  · **`diag_encontro` contaba filas.** Ahora suma `n_casos` y muestra
    `[187 casos]` al lado de las 98 filas, y `control:patas_dolar_sin_pedir(133)`
    deja de verse igual que un control con un solo caso.

#### La regla que queda

**Un contador que no se mueve cuando el trabajo avanza es un contador
equivocado, aunque cada número que muestra sea cierto.** Los 98 eran correctos
las tres veces. El problema no era la exactitud, era la granularidad: se estaba
midiendo en una unidad (el control) que no cambia cuando pasa lo que sí cambia
(el caso). Si algo se arregló y ningún número lo refleja, el contador no está
midiendo el trabajo.

Y un test menos: `test_si_salud_no_se_puede_evaluar_no_caduca_nada` era un grep
de `'estado == "ok"'` sobre el fuente y **se rompió al renombrar una variable,
sin que el comportamiento cambiara**. Un test que se cae por un rename y no se
caería por un `!=` cuida el texto, no la regla. Ahora ejerce el predicado con los
tres casos: verde tacha · rojo se queda · «no pude mirar» nunca es «resuelto».

### 0.cd APLICAR 5 Y QUE LA PANTALLA MUESTRE LOS MISMOS 98 (2026-08-22)

El user aplicó los 5 emisores de FCI —se escribieron, se verificaron, `5/5`— y
`diag_encontro` devolvió **exactamente los mismos 98 hallazgos, número por
número**. Para el que mira, eso es indistinguible de que el botón no haga nada.

Y el arreglo estaba bien. **Entre el dato y la pantalla hay CUATRO capas de
foto, y arreglar el dato no tocaba ninguna:**

    jobs.controles_datos (16:30 UTC)  →  manager.controles_datos
    salud.evaluar()                   →  lee esa tabla
    jobs.av_agent (de noche)          →  escribe av_agent_hallazgos
    la pantalla                       →  lee esa foto

#### La regla ya estaba escrita. A SALUD no se le había aplicado.

Textual, en `_hallazgos_ultima_corrida`, desde el incidente del DICP:

> *Todo criterio que decida si algo se MUESTRA tiene que poder evaluarse en la
> LECTURA.*

Y se venía cumpliendo para `falta_en_base` (¿ya está en `mercado.curvas`?),
`hueco_de_curva` (¿ya existe la curva del ajuste?), `sin_flujo` (¿ya tiene
cronograma?) e `ignorados`. **Para `salud` no** — que es el tipo con más filas
de la pantalla (10 controles + 3 jobs, y detrás de cada control hay hasta 133
casos).

Es la QUINTA vez que aparece el mismo síntoma: TZXM8, BADLAR, el IGNORAR, el
DICP con «✔ cronograma escrito» y el hallazgo intacto, y ahora éste.

#### Dos cambios, los dos GENERALES

**(1) Aplicar vuelve a mirar.** `hacer.aplicar()` re-corre el control de cada
acción aplicada, **derivado de `a.sobre`** — no hay lista que mantener y una
acción nueva lo hereda sola. Por la MISMA puerta que el cron
(`_diff_y_persistir`), así que un arreglo a mano y la corrida nocturna dejan
idéntico estado. Si el re-chequeo falla, **el arreglo no se deshace**: el dato ya
se escribió y se verificó; no poder refrescar la pantalla es peor información, no
un arreglo fallido, y se dice.

**(2) La pantalla coteja SALUD contra el estado vivo.** `salud.evaluar()` —la
única función que arma ese estado, no una copia— cacheada 45s: menos que el poll,
así que lo que se arregla se ve en la lectura siguiente, y suficiente para que
abrir el modal diez veces no pague diez evaluaciones.

⚠️ **La guarda que importa**: el predicado exige `estado == "ok"`, **no la
ausencia del id**. Si `evaluar()` falla devuelve `{}`, y un id ausente se trata
como NO resuelto. Tratarlo al revés vaciaría ENCONTRÓ justo el día que SALUD está
caído — dejar el tablero en verde el día que está más ciego es la mentira más cara
que puede decir una herramienta de integridad, y es literalmente el mismo error
que ya se corrigió en `sincronizar(evaluados=…)`.

Congelado por `tests/unit/test_av_agent_cotejo.py`: los cinco cotejos, que SALUD
use la función real y no una copia, que un fallo no vacíe la pantalla, y que el
re-control se derive de la acción.

### 0.ci CI ROJA EN `main`: dos contratos de capas rotos (2026-08-22)

Las últimas 6 corridas de CI en `main` fallaban en `lint-imports` — y con ese
paso rojo, **Pytest ni corría**: cualquier regresión nueva entraba sin red. Dos
contratos rotos, y cada uno pedía un arreglo distinto porque las causas eran
distintas:

- **`core.dependencias → api.services.jobs_catalogo`.** La correlación
  label→módulos (§0.af) delegaba en el parser del crontab, que vivía en
  `api/services` — y core no puede importar del proyecto. El parser es parsing
  PURO de un archivo que viaja con el deploy (ni base ni red), así que su lugar
  era `core/`: ahora vive en **`core/crontab.py`** y `jobs_catalogo` importa de
  ahí con el nombre que ya usaban sus llamadores (`_parse_crontab`). Un solo
  parser (REGLA #9), capa correcta, cero llamadores tocados.
- **`av_agent_seguridad → api.superficie → fastapi`.** Acá el arreglo NO es
  mover código: `api/superficie.py` es la ÚNICA implementación de la
  enumeración de rutas (la regla «no la reimplementes» existe porque la copia
  veía 37 de 541) y vive pegada a FastAPI por naturaleza — inspecciona la app
  montada. Se declara la excepción en `.importlinter` con su porqué, igual que
  la que ya tenía `_grupos_scope`.

La lección es de proceso: los dos imports entraron en commits que CI ya no
podía frenar porque el paso anterior ya estaba rojo. **Una CI roja no es un
estado: es una puerta abierta** — todo lo que se pushea mientras tanto entra
sin que nadie lo mire.

#### Y detrás de la puerta había DOS tests rotos más

Con `lint-imports` verde, Pytest volvió a correr — y cazó dos fallas que
entraron durante la ventana ciega. Las dos son la misma enfermedad ya
bautizada en §0.ay y §0.ce: *un test que congela la implementación, no la
intención*.

- **`test_lo_que_ARRASTRA_no_entra` era flaky por HORA DEL DÍA.** Usaba
  `AHORA − 21 h` (las 21 horas del incidente real) esperando que cayera
  «ayer», pero el corte del día es la medianoche ART: entre las 21:00 y las
  24:00 ART esas 21 horas caen adentro de HOY y el test fallaba — tres horas
  por día, todos los días. CI de las 20:54 ART lo pasó por seis minutos. Ahora
  el timestamp se arma contra `_arranco_el_dia()`, el corte real.
- **`test_se_ven_TODAS_las_rutas` congelaba un detalle de la VERSIÓN de
  FastAPI.** Afirmaba `rutas() > app.routes × 5`, que presume los envoltorios
  `_IncludedRouter`; con la 0.136.x pineada `app.routes` viene PLANO (las 562
  directas) y el test fallaba justo cuando no hay nada escondido. Ahora
  verifica la intención en los dos mundos: con envoltorios, superficie ve
  mucho más que el primer nivel; plano, no puede ver ni una APIRoute menos.

#### ⚠️ Y el hallazgo de fondo: EL ENTORNO REAL Y EL PIN NO CORREN LA MISMA FASTAPI

Ese segundo test destapó algo más grande, verificado acá y no supuesto: la
clase `_IncludedRouter` **no existe** en la FastAPI que pinea
`requirements.txt` (0.136.x aplana `app.routes` — medido con una app mínima).
Pero el 37/541 de §0.s se midió EN PROD con envoltorios: **el entorno donde
corre el sistema y el que instala CI no son la misma FastAPI.** REGLA #9(B) a
nivel entorno: dos mundos sin árbitro, cada uno coherente consigo mismo.

Consecuencia concreta: `gen_mapa_app` genera un mapa DISTINTO según dónde
corra — regenerado en el entorno plano, la tabla de 31 routers colapsa a 1
(`(raíz)`) porque la atribución por router viaja en los envoltorios. Por eso
el `--check` del mapa no puede dar verde en CI mientras el doc se genere en el
entorno real (y el mapa regenerado en CI sería PEOR, así que no se regeneró
acá a ciegas). Tres cosas quedaron hechas:

  1. **CI reordenada: Pytest corre ANTES del check del mapa.** Un doc
     desincronizado no puede volver a tapar el resultado de los tests — que es
     literalmente lo que pasó estos dos días.
  2. **`scripts/diag_entorno`** (read-only): compara instalado contra pineado
     en los paquetes clave y dice en qué mundo cae `app.routes`. Correrlo en
     el Droplet contesta qué versión manda. De paso ya midió algo acá:
     `psycopg` está **sin pinear** en requirements.
  3. ~~La decisión queda abierta~~ → **RESUELTA CON LA MEDICIÓN** (mismo día,
     abajo).

#### La medición REFUTÓ la hipótesis: el Droplet es PLANO

`diag_entorno` corrido en prod: **FastAPI 0.136.1 == pin, `app.routes`
PLANO.** O sea que prod, CI y el sandbox son el MISMO mundo — el entorno
anómalo con envoltorios es la **máquina local de Windows** (donde se generó el
mapa de 31 routers y donde se midió el 37/541 de §0.s). La hipótesis «prod
tiene los envoltorios» era razonable y estaba equivocada, y decidir el pin
sobre ella habría alineado CI contra el entorno equivocado. REGLA #2: la
medición antes que la decisión.

Con eso el arreglo cambió de forma:

- **`superficie` aprendió el mundo plano** (`_bajar_plano`): la atribución de
  routers se reconstruye por **IDENTIDAD DE ENDPOINT** — la función declarada
  es el mismo objeto en la ruta copiada al tope y en el router que la declaró
  (REGLA #9A: identidad por ficha, no por string) — y la etiqueta sale de
  restarle al path completo el tramo declarado. Verificado: reproduce los
  **31 routers exactos** de la tabla. Los gates ya eran world-independientes
  (FastAPI plano fusiona las dependencies del include en cada ruta, así que
  `_gates_propios` ve la misma unión que el otro mundo arma a mano).
- **El mapa PLANO pasa a ser el canónico** (regenerado: 4 filas cambiaron solo
  en el conteo de «gates extra» — cada mundo expande sub-dependencias con
  distinta profundidad). CI y prod lo reproducen; el `--check` de CI queda
  verde.
- **`psycopg` quedó pineado a 3.3.4** — la versión medida en el Droplet; era
  el único drift real que mostró el diag. `psycopg-pool` entra a la lista del
  diag para medirse en la próxima corrida antes de pinearse (REGLA #2).
- **Pendiente para el user, una sola vez (REGLA #6)**: reinstalar el venv
  LOCAL de Windows desde `requirements.txt`, así el mapa regenerado ahí vuelve
  a coincidir con el canónico. Mientras tanto, regenerarlo desde la máquina
  local va a mover esas 4 filas — no está roto, es el mundo viejo.

### 0.cj EL FRONT TAMBIÉN TIENE CAPA: la red se toca desde UN lugar (2026-08-22)

Dos sesiones de Claude discutieron el refactor del modal y **las dos midieron
antes de opinar** — vale dejar el veredicto porque fija la arquitectura del
front del agente:

- La hipótesis «67 useState con su propia copia de los datos» era EXAGERADA:
  medido, el modal tiene 46 componentes y ~1,5 estados por componente, casi
  todos de PANTALLA (qué tab, qué filtro, qué está abierto) — eso está bien y
  se queda donde está.
- Los dos bugs shippeados de esa familia («voté y los botones volvieron», «el
  informe desapareció al cambiar de tab») no eran localidad de estado: eran
  **relecturas que faltaban**. Se habían arreglado punto por punto.
- Lo real eran **15 llamadas a la red adentro de componentes que se desmontan**
  y, más de fondo, que el buen patrón (escribir → releer) era una CONVENCIÓN:
  cada botón nuevo podía olvidarla, y dos ya la habían olvidado.

**Lo que quedó construido** (`src/components/av-agent/datos.tsx`): la capa de
datos del modal, con TRES verbos que significan cosas — la misma idea que
separa `simular` de `aplicar` en el backend:

    leer(url)                   GET — no cambia nada
    llamar(url, body)           POST que CALCULA (explicar, simular, lanzar el
                                masivo) — no relee nada
    escribir(url, body, relee)  POST que MUTA — declara QUÉ recursos invalida
                                y los relee al confirmar. La relectura es el
                                CONTRATO del verbo, no una convención del que
                                llama: el bug del voto sin huella deja de poder
                                escribirse.

Los recursos (vista · control · centinela · agenda · skills · evaluación ·
sabe) tienen UN dueño, sobreviven al cambio de tab y «no pude leer» conserva
el dato viejo con el error aparte — nunca un `null` silencioso que se dibuje
como «no hay nada» (§0.be, del lado del navegador).

**Y la regla es MECÁNICA, no un comentario**: `eslint.config.mjs` prohíbe
importar `fetch-json` en el modal fuera de la capa — el import-linter del
front, el mismo día que el del backend volvió a verde (§0.ci). El próximo
fetch suelto no pasa el lint.

Dos cosas que se decidieron NO hacer, con el porqué:

- **Partir el archivo de 5.400 líneas NO es la cura y no se hizo en esta
  pasada.** La advertencia de la otra sesión es correcta y es REGLA #9: veinte
  archivos sin regla son veinte lugares donde puede nacer una segunda
  definición. Primero la capa y su regla (hecho); el corte por tab es higiene
  y va después, tab por tab — el mismo playbook que la migración de las 22
  tablas (§0.bc).
- **El front sigue sin derivar NADA**: acción, estado, atendido, nombre — todo
  viene resuelto del backend en cada recurso. La capa lee, escribe y relee;
  jamás «actualiza a mano» una copia local con lo que el cliente cree que
  quedó.

#### El corte por tab (mismo día, con OK del user)

Con la capa puesta y la regla de lint vigilando, el archivo de 5.400 líneas
**se partió** — mecánicamente, sin tocar una letra adentro de ningún
componente (un script movió cada bloque top-level entero, con sus comentarios,
y `tsc` dictó los imports):

    av-agent-modal.tsx   690   la cáscara: botón, header, wiring de tabs
    av-agent/
      datos.tsx          150   la capa (§ arriba) — el único que toca la red
      tipos.ts           726   types + labels + helpers puros, el contrato UNA vez
      piezas.tsx         478   lo compartido entre tabs (Chequeos, PanelHacer,
                               Marcado — Chequeos lo usan SKILLS y ENCONTRÓ)
      tab-ahora.tsx      686   lo del día + la interrupción + preguntas y avisos
      tab-hallazgos.tsx 1933   la cocina (lista, prioridad, seguimiento, masivo)
      tab-historial.tsx  231 · tab-agenda.tsx 181 · tab-skills.tsx 336 ·
      tab-control.tsx    156

Qué compra: un cambio en ENCONTRÓ toca SOLO su archivo y no puede romper las
otras tabs; el que edita lee 300 líneas y no 5.400. Y el riesgo que la otra
sesión marcó con razón —veinte archivos son veinte lugares donde nace una
segunda definición— lo neutraliza la capa: una tab partida **no puede**
fetchear ni derivar por su cuenta, el lint no la deja. Verificado: `tsc`
limpio, build de Next OK, y el lint quedó con UN solo error — el mismo
preexistente de antes del corte.

### 0.ck EL VOTO QUE NO SE RECORDABA NUNCA — la identidad del caso (2026-08-22)

> *«Me voy de la tab, vuelvo, y otra vez me aparecen como si no se marcó nada.
> Ya me gasté miles de tokens y sigue pasando. Hace que no tenga valor
> prácticamente nada.»* — user, con los votos de motores y tablas volviendo
> intactos en cada recarga

Tenía razón y esta vez el bug estaba en el BACKEND, un nivel más abajo de los
dos que ya se habían arreglado (§0.bn, §0.cb). **La identidad del caso estaba
escrita con dos criterios**:

    votar()               guarda  caso.upper()      → «MANAGER.SALUD_EVENTOS»
    ya_votados()/es_ruido devuelven lo guardado
    la vista busca        (ticker).strip()          → «manager.salud_eventos»

Un BONO matcheaba **de casualidad** (ya viene en mayúscula) — por eso los
votos de bonos sí se recordaban y los de sistema jamás. Y el dedup
(`_voto_previo`) leía SIN upper → tampoco encontraba el previo → cada click
escribía una fila nueva (el «0/9» que se veía en pantalla: nueve votos
guardados del mismo caso). REGLA #9 en su forma más pura: cero errores, cero
logs, cada mitad coherente consigo misma.

**El arreglo es UNA normalización con dueño**: `av_agent_evals.clave_caso()`
— la usan el que escribe y TODOS los que leen. Congelado por
`test_evals_clave_caso`: un `.upper()` suelto sobre el caso vuelve a fallar el
build (el test lee el fuente ejecutable, sin comentarios NI docstrings — que
citan el bug por nombre).

Con esto, tres quejas se resuelven de una: el voto persiste al cambiar de tab,
«✖ es ruido» SACA la fila de verdad (el filtro ya existía y no matcheaba), y
el dedup vuelve a dedupear.

#### De la misma tanda, tres arreglos más

- **El badge de la barra cuenta lo que la tab muestra** («abajo marca 7 y
  entrás y son 10»): había DOS badges con otros números (`cent.sin_ver` y las
  preguntas). Ahora es UNO = `nAhora`, el mismo número de la pestaña AHORA —
  §0.bo aplicado un nivel más arriba.
- **Los sellos de hora llevan FECHA y zona correcta** («les falta la fecha»):
  los motivos persisten `· 23:31` y días después se leen como de hoy — y el
  de `sin_escribir` encima estampaba la hora UTC sin convertir (23:31 que en
  la mesa eran 20:31). Los tres selladores emiten `dd/mm HH:MM` en ART; las
  filas viejas se re-estampan cuando el detector vuelve a correr (§0.ba).
- **El informe masivo se puede CERRAR** («no se puede cerrar, está 100%
  estático»): `visto_at` en `av_agent_runs` + `POST /masivo/visto`. Cerrado
  queda en UNA línea reabrible — no se borra: una corrida de minutos no puede
  ser irrecuperable. La marca vive en el backend: recargar no lo revive. Un
  run corriendo no se cierra (se frena primero).
- **«SIN PUERTA» deja de decir lo mismo para tres cosas distintas** («no
  termino de entender por qué no tiene que hacer nada»): infraestructura
  (motores/proveedores) dice *se mira y se decide afuera*; observaciones de la
  base (tabla nueva/quieta) dicen *se contesta con ¿TE SIRVE VERLO? — «✖ es
  ruido» la esconde* (que ahora funciona); y solo el resto queda como puerta
  que falta construir.

### 0.cl LA DEUDA DE §0.cg SE PAGÓ EN VIVO — y lo decidido deja huella (2026-08-22)

> *«Si ya están resueltos, ¿por qué siguen apareciendo? ENCONTRÓ tiene que ser
> para lo pendiente. Voy tachando cosas y nada pasa a historial.»* — user, con
> 11 filas de `pata_equivocada` cuya propia cadena decía «ya no aparece: se
> resolvió solo»

Pasó TEXTUAL lo que §0.cg dejó escrito: *«tres reglas más son hechos de base y
todavía no tienen cotejo en la lectura — si se arreglan hoy, van a quedarse en
pantalla igual»*. Se arreglaron hoy y se quedaron en pantalla. La lista de
deuda hizo su trabajo (la falla estaba NOMBRADA, no olvidada) y ahora quedó
**vacía**:

- **`pata_equivocada`** caduca cuando el master YA apunta al símbolo sugerido.
  Se compara contra la **columna** `instrumento` (la que gana — §0.y), que
  entra en la MISMA query que la vista ya hacía. Sin `sugerido` en la
  evidencia no se caduca: «no sé» nunca es «se arregló».
- **`sin_ejes`** caduca con `ejes_de_doc` (el predicado del detector) sobre el
  doc que la misma query trae.
- **`moneda_flujo_contradice`** caduca con `moneda_flujo_esperada` (ídem). Si
  la esperada no se puede calcular, NO caduca.

El test de la deuda pasó de listar 3 a exigir **cero** — y uno nuevo exige que
los tres cotejos EXISTAN en `_caduco`: sacar el nombre de la lista sin
escribir el cotejo sería el olvido con papeles en regla.

#### Lo decidido deja huella: VOTASTE en HISTORIAL

El voto apagaba la fila y el rastro no vivía en NINGUNA pantalla — «ni
siquiera queda registrado en ningún lado». Ahora `vista()` publica los últimos
votos (`av_agent_evals.ultimos()`, cacheado 45s e invalidado al votar) y
HISTORIAL → YA DECIDIDO abre con **VOTASTE**: caso, causa, qué contestaste
(✔ acertó / ✖ es ruido / …), la nota y cuándo.

#### Y dos de pantalla

- **El filtro vacío dice la verdad**: filtrar un tipo cuyos casos ya
  atendiste todos mostraba el vacío pelado. Ahora distingue tres casos: todo
  atendido en general · «los N de ESTE filtro ya pasaron por tus manos, están
  en ¿AGUANTAN?» · el filtro de verdad no matchea nada.
- **¿AGUANTAN? se explica sola** («no está claro para qué es esta vista»):
  arriba de todo dice qué es — la sala de espera de lo que ya se tocó. EN
  PRUEBA = arreglos aplicados vigilados por hitos (1·2·3·7·14·30 días; aguantar
  30 cuenta como acierto verificado en el eval set); YA LO ATENDISTE = lo
  votado/aplicado hasta que el detector confirme. Lo confirmado desaparece
  solo; lo que VUELVE salta primero en AHORA.

### 0.f El eval set (2026-08-17)

`mercado.av_agent_evals` — un ✔/✖ humano por diagnóstico, con la causa correcta
cuando falla. **El dataset ya existía y se estaba tirando**: cada vez que alguien
abre un diagnóstico y decide, emite un juicio sobre si la causa era la correcta.

- **Un ✖ sin motivo se rechaza**: de «está mal» no se aprende nada.
- `MIN_VOTOS` separa un porcentaje con respaldo de uno con tres votos — 2 de 2 no
  es «100% de acierto», es «casi no hay evidencia».
- `candidata_a_auto` **no es un permiso**: es lo que el número habilita a
  discutir. La lane automática se prende a mano, siempre.

#### EL BOTÓN, que era lo único que faltaba (2026-08-19)

La tabla y los dos endpoints estaban desde el 2026-08-17 y **nadie los llamaba**:
cero fetches en el front, cero llamadas desde jobs o scripts. Las únicas dos
apariciones de «eval» en el modal eran comentarios.

O sea que **la compuerta de toda la autonomía era una tabla a la que no había por
dónde escribir**, y las capas 3 a 7 del roadmap estaban trabadas por la pieza más
chica de todas. No faltaba construir el eval set: faltaba el botón.

**Va donde está el diagnóstico**, no en una pantalla aparte. El juicio ya se
emite —cada vez que alguien lee un hallazgo y decide, dice si la causa era la
correcta— y lo que faltaba era guardarlo. Una pantalla de votación separada pide
que alguien se acuerde de ir, y *lo que no está en el camino no se hace*: el
dataset se seguiría tirando, solo que con una tab más.

Tres decisiones que no son obvias:

  · **El ✖ abre el campo en vez de votar.** No es fricción: un «está mal» suelto
    no sirve para reescribir la regla, así que sería un voto que ocupa lugar y no
    enseña nada. El backend lo rechaza igual; el front no lo deja llegar.
  · **El voto va en TODA fila, tenga acción o no.** Lo que se mide es si el
    DIAGNÓSTICO acertó. Restringirlo a los accionables dejaría sin medir justo a
    los que todavía no sabemos si vale la pena automatizar.
  · **Votar no cambia nada del sistema.** Es una anotación sobre el AGENTE, no
    sobre el bono; mezclarlas haría que corregir el diagnóstico parezca arreglar
    el problema.

Y dos que hubo que adaptar antes de enchufarlo: el dominio `sistema` no existía
—el payload lo rechazaba con `^(bono|salud)$` y el botón habría dado 422 sin
motivo aparente— y el dominio ahora **lo decide el backend** y viaja en el
hallazgo (`dominio_eval`), igual que `accion` y `de_quien`: deducirlo del tipo en
el front habría creado el duplicado que la REGLA #9 persigue, antes de estrenarlo.

**La medición vive adentro de SKILLS**, no en una tab nueva: SKILLS es *«lo que
el agente sabe hacer»* y cuánto acierta es un **atributo de eso**. Separarlos
dejaría el catálogo prometiendo capacidades sin decir cuáles funcionan. Marca
`sin evidencia` con menos de `MIN_VOTOS` y `◆ a discutir` cuando el número
habilita la conversación — que sigue sin ser un permiso.

Con cero votos **no se muestra vacío**: dice que todavía nadie votó y cuántos
hacen falta. Y si la medición no se puede LEER lo dice distinto, porque *«no pude
preguntar» no es «no hay votos»*.

### 0.g LA MEMORIA: nada se desperdicia (2026-08-17)

Pedido del user, y es el que ordena todo lo que viene: *«todo lo que pase de ahora
en adelante tiene que servir para alimentar al modelo… no puede quedar nada
desperdiciado: tiene que quedar todo, cómo se va construyendo la solución, los
errores que detecto, cómo se fue modificando. Porque ahora es bonos, pero después
está SALUD y van a venir más cosas.»*

Y tenía delante el caso perfecto. En OLC3O el agente dijo, **en la misma
pantalla**:

    lente 7  →  «ninguna fuente local tiene un precio mayor que 0»
    paso 13  →  «precio 137.280,0000 (snapshot)»

**No era un bono mal cargado: era el agente contradiciéndose** — las lentes
recibían un dict sin la clave `precio`. Eso es más grave que un dato malo, porque
destruye la confianza en todo lo demás que dice, incluido lo que está bien. Y sin
memoria, la lección de ese bug vivía en un chat y se perdía.

**Las tres piezas** (`api/services/av_agent_memoria.py`):

| | Qué hace | Qué agujero tapa |
|---|---|---|
| **TRAZAS** | guarda cada diagnóstico entero: las 8 lentes, la causa, el contexto | se calculaba y se tiraba al cerrar el modal — sin el registro de lo que dijo ayer no hay forma de saber si hoy dice algo mejor |
| **CONTRADICCIONES** | el agente se audita: dos lentes que afirman cosas incompatibles | el bug de OLC3O pasó tests, lint y una lectura humana |
| **LECCIONES** | síntoma → causa raíz → **qué se cambió** → commit | el aprendizaje vivía en un chat |

**Las lentes declaran HECHOS, no solo prosa.** La primera versión del detector
comparaba el TEXTO de las observaciones y no cazaba nada: un sinónimo lo rompía.
Ahora cada lente publica lo que afirma en máquina (`hechos: {"precio": None}`) y
la comparación es exacta. **Lo que hay que detectar son justamente las
incoherencias que ya se le escaparon a una lectura humana**, así que el detector
no puede depender de cómo esté redactada la frase.

**Las lecciones se muestran DENTRO del diagnóstico**, antes de la conclusión, y
no en un doc aparte: una lección sirve en el momento en que alguien está por
decidir, no cuando se le ocurra ir a buscarla. Lo mismo con **CASOS PARECIDOS**
—los otros bonos donde el agente dijo la misma causa, con sus votos del eval
set—: es *exemplar learning* sin modelos ni vectores, y contesta la pregunta que
una persona haría primero, **«¿esto ya lo vimos?»**.

**El ciclo completo, cerrado:**

    detecta → razona (8 lentes) → se audita → propone → verifica → aplica
        ↓                                                              ↓
      TRAZA  ────────────────────────────────────────────────────►  EVAL (✔/✖)
        ↓                                                              ↓
      CASOS PARECIDOS  ◄──────────  LECCIÓN (qué se cambió)  ◄─────────┘

Las siete lecciones de esta sesión ya están sembradas
(`python -m scripts.sembrar_lecciones`) — **todas salieron de errores reales**, y
tres las cazó el user mirando la pantalla, no un test. Eso también se registra:
`detectado_por` distingue `user` de `agente` de `test`, porque saber **quién
encuentra los errores** dice dónde está el punto ciego.

---

## 1. Qué es y qué no es

El **AV Agent** es un agente de **integridad de datos**: compara nuestra verdad
contra una fuente externa y **propone la corrección con evidencia**. Su primer
dominio es **renta fija** (`mercado.curvas` contra 1816), pero el verbo es
genérico a propósito — ya lo aplicamos tres veces a mano (`jobs/ficha_1816` con
emisores, `jobs/validar_instrumentos` con especies, `jobs/assets_autofill` con la
herencia de FCI). Diseñarlo como "agente de curvas" habría garantizado cinco
agentes que no comparten nada; el dominio es un plug-in.

**Las tres tareas del pedido original (user, 2026-08-16):**

1. Bonos que existen en 1816 y no en nuestra base → detectarlos y darlos de alta
   **con su cuadro de flujos**, respetando los ejes y la shape de flujo que ya usa
   el master.
2. Bonos nuestros **sin flujo** → buscarlos en 1816 y completarlos.
3. **Tasas que dan mal** → saber decir *"esta TEA está mal porque el flujo / la
   pata / la escala del bono está mal"*.

**Lo que NO es.** No es un copiloto. Un copiloto espera que le pregunten; el
AV Agent tiene trabajo propio y lo hace de noche aunque nadie lo mire. Esa es toda
la diferencia y es la que decidió el encuadre.

### 1.b Dónde está la IA (y dónde NO)

Decisión de diseño, tomada al empezar: **los puntos 1 y 2 no llevan IA.** "Qué
hay en 1816 que no tengo" es una resta de conjuntos y "bajar un cashflow" es una
llamada HTTP. Meter un modelo ahí viola la regla de oro 1 de `QUANTAI.md` (la IA
nunca es la fuente de un número) y agrega riesgo sin agregar capacidad.

**El punto 3 SÍ es el agente**: *"esta tasa está mal por tal razón"* no tiene
algoritmo — es encadenar evidencia (precio, pata, `moneda_flujo` vs cartera,
escala del flujo vs escala del precio, duration, CER) hasta una hipótesis de causa
raíz. Hay precedente directo en el repo: `triage_incidente` y `salud_diagnostico`,
las dos tareas `pro` con thinking.

Por eso la arquitectura es **pipeline determinista con un cerebro en el medio**,
no un ReAct improvisando sobre datos financieros. Los pasos 1 y 2 son las *tools*
del agente, no su inteligencia.

---

## 2. Lo que YA existe y se reusa (verificado 2026-08-16)

**Nada de esto se reescribe.** El agente es en buena medida cableado de piezas que
ya funcionan y que ya validó el uso.

| Pieza | Qué aporta | Estado |
|---|---|---|
| `core/ai.py::completar_con_tools` | loop de function-calling con presupuesto, ruteo, traza SQL | operativo |
| `core/llm.py` | DeepSeek + OpenAI, retry, dialecto por proveedor | operativo |
| `core/mercado_1816.py` | cliente 1816: auth, throttle, backoff 429, `/curvas`, `/instrumentos`, `/cashflow` | operativo |
| `scripts/diag_1816_cashflow.py::censar()` | censo de las 28 curvas → universo real | a **promover a módulo** (E1) |
| `core/curvas_ejes.py` | tabla curva-de-1816 → ejes (`emisor_tipo`/`moneda`/`ajuste`/`ley`) | operativo |
| `engines/curvas.py::rama_calculo` | qué fórmula le toca a cada bono, desde los ejes | operativo |
| `api/services/debug_curva.py::debug_calculo_tea` | replica el motor para 1 ticker y compara calculado vs persistido | operativo — **base del simulador (E2)** |
| `POST /api/manager/bonos` (`svc.upsert_bono`) | alta/edición con validación de ejes | operativo — **es la puerta de escritura del agente** |
| `POST /api/manager/bonos/parse-flujos` | normaliza flujos a la shape del tipo (pct vs absoluto) | operativo |
| `GET /api/manager/bonos/sin-flujo` · `/sin-tasa` | los dos conciliadores que hoy nadie mira | operativo — **es la bandeja (E5)** |
| `api/services/salud.py` + modal de SALUD | canal de aviso con el criterio correcto (solo transición nueva y sin ver) | operativo |
| `manager.job_runs` + `JobRunLogger` | observabilidad del job | operativo |
| `evals/` | carpeta de sets de evaluación (hoy: asistente, copiloto_vista) | operativo |

> **El agente escribe por la MISMA puerta que el humano.** No hay un camino de
> escritura para el AV Agent y otro para la mesa: los dos pasan por `upsert_bono`.
> Si hubiera dos, se desincronizan y nadie se entera hasta que un flujo entra con
> otra shape.

---

## 3. Los números que sostienen el diseño (MEDIDOS, no estimados)

Todo esto está medido en prod y documentado en `VISTA_RESEARCH.md` §4.9 —
corridas reales en el Droplet del 2026-08-15.

| Hecho | Valor |
|---|---|
| Universo de 1816 | **887 tickers vigentes** en 28 curvas (partición limpia: ninguno en dos curvas) |
| Nuestro master | 222 bonos en `mercado.curvas` |
| En los dos lados | **251 tickers** (1816 ∩ Manager) |
| Nuestros bonos que 1816 tiene | **212 / 222 = 95,5%** |
| Cobertura de cashflow | **98,6%** (muestra estratificada de 70/887; 0 vacíos, 1 error) |
| Horizonte del cuadro | **completo desde emisión** (AE38 devuelve sus 34 cupones) |
| Escala | **no hay una sola** — por VN 100 en bonos por paridad, en nominales en algunas ONs. **Coincide con la nuestra instrumento por instrumento** |
| Costo del barrido diario | **29 créditos** (1 `/curvas` + 28 `/instrumentos`) |
| Costo de un bono nuevo | **~14-17 créditos** (1 por cupón) |
| Costo mensual estimado | **~600-800 créditos contra un tope de 3.100.000** = 0,02% |

**El costo no es una restricción de este proyecto**, y eso es porque el diseño
usa SOLO `/curvas` + `/instrumentos` + `/cashflow`, y **nunca** `/indicadores` ni
`/series`, que son los caros. Los precios y las tasas los sigue calculando el
sistema (punto 5 del pedido original del user).

**Dato que ordena todo:** los flujos que hoy están en `mercado.curvas` **salieron
de 1816** — los cargó el user a mano desde ahí. Esto no es adoptar una fuente
nueva: es conectar la que ya se venía usando.

---

## 4. Decisiones ABIERTAS (pendientes del user — no se resuelven por default)

> Estas tres no se deciden por conveniencia del que codea. Cada una cambia el
> comportamiento del agente en producción.

### D1 — Alcance del universo
El pedido dice "soberanos" en el punto 1 y "todo mi universo" en el punto 2. Son
escalas distintas: **104 soberanos** en 1816 contra **887 totales** (667 de ellos
corporativos). ¿El agente nace mirando soberanos y escala, o mira todo desde el
día uno?
**Estado: ABIERTA.**

### D2 — Política de conflicto (el caso AER9O)
Medido en §4.9: para el cupón del 2026-08-19 nuestro master dice amortización
**48.215,20** y 1816 dice **49.710,88** — **3,1%**, y no es redondeo (los otros
cuatro cupones cierran dentro del 1%). *Hipótesis sin verificar*: es una ON de
amortización indexada y cada fuente la ajusta a distinta fecha.

Cuando las dos fuentes difieren, **¿quién gana?**
- **1816 como fuente de verdad** → el agente pisa y la base converge sola. Hay
  precedente: `jobs/ficha_1816` hace exactamente eso con los emisores.
- **1816 como segunda opinión** → el agente reporta y decide un humano.

**No son el mismo riesgo que el emisor**: un flujo mal escalado rompe el chart
entero y contamina el AuM vía el join a `portafolio.assets`; un emisor mal escrito
no. Por eso no pueden compartir política.
**Recomendación: segunda opinión en v1**, y mirar AER9O con la mesa antes de
darle más poder. **Estado: ABIERTA.**

### D3 — Alcance del diagnóstico (punto 3)
¿Barrido proactivo de las 222 curvas todas las noches, o herramienta que se le
apunta a un bono puntual? El barrido es más agente pero necesita primero las
reglas de sanidad — que son el `jobs/curvas_healthcheck.py` que `SALUD_CURVAS`
§7 propone y **nunca se hizo**. La herramienta puntual arranca antes.
**Estado: ABIERTA.**

---

## 5. Etapas

Sin fechas a propósito: el orden es por **dependencia y validación**, no por
calendario. Ninguna etapa arranca sin que la anterior esté validada por el user.

**Las tres primeras no tienen una línea de IA, y es deliberado**: un agente que
diagnostica sin poder medir si acertó no es un agente, es un generador de
opiniones. El eval va ANTES del modelo.

| # | Etapa | ¿IA? | ¿Escribe en prod? | Estado |
|---|---|---|---|---|
| E0 | Doc vivo + decisiones abiertas | no | no | ✅ **hecho** |
| E1 | El espejo (detectar, sin escribir) | no | solo tabla propia | ✅ **calibrado en prod (64 hallazgos)** |
| E1.c | El agente PREGUNTA | no | tabla propia | ✅ **hecho** |
| E1.d | El modal (barra inferior, admin-only) | no | tabla propia | ✅ **hecho** |
| E2 | El simulador + aplicar el alta | no | **sí, con click humano** | ✅ **hecho** |
| E3 | El set de control (evals) | no | no | pendiente |
| E4 | El cerebro (diagnóstico) | **sí** | no | pendiente |
| E5 | La bandeja (Manager → BONOS) | — | sí, **con aprobación humana** | pendiente |
| E6 | La primera lane automática | — | sí, **automática y reversible** | pendiente |
| E7 | Aprendizaje + digest | sí | no | pendiente |

### E0 — Doc vivo y decisiones abiertas ✅
Este archivo. Absorbe el diseño de `VISTA_RESEARCH.md` §4.10 (que queda como el
registro de la medición) y lo convierte en plan ejecutable con decisiones
explícitas. **Validación:** el user lo lee y confirma que es el sistema que quiere.

### E1 — El espejo (read-only, sin IA) — CODEADO, falta calibrar

**Qué se entregó:**

| Archivo | Qué es |
|---|---|
| `core/mercado_1816.py::censar()` | el censo de las 28 curvas, **promovido** desde `scripts/diag_1816_cashflow` (un job de prod no puede depender de un diag, que por la REGLA #5 se borra al cumplir). El diag quedó con un alias. |
| `api/services/av_agent.py` | los **tres detectores**, lógica PURA (sin base, sin red, sin FastAPI) + `relevar()` que orquesta |
| `jobs/av_agent.py` | el job: censo → detectores → `mercado.av_agent_hallazgos`. `--dry-run`, `--alcance`, `--detalle` |
| `mercado.av_agent_hallazgos` (`sql/schema.sql`) | append-only **por corrida**, con la evidencia congelada y TTL de 60 corridas |
| `tests/unit/test_av_agent.py` | 15 tests — **9 de ellos afirman que algo NO se reporta** |

**El ALCANCE es un parámetro, no una constante** (`--alcance soberanos` por
default): así la decisión **D1**, que sigue abierta, es un flag y no un rewrite.

**Tres exclusiones deliberadas** — son lo que separa una lista útil de una que
nadie mira a las tres semanas:
- los ajustes que el motor **no calcula por diseño** (`tamar`/`badlar`/`tpm`/
  `caucion`, que caen en el `else` de `engines/curvas.py`): reportarlos sería
  denunciar todas las noches una decisión de arquitectura — 18 bonos de ruido fijo;
- las tasas ya marcadas **ruido por duration**, con el MISMO predicado que la
  vista (`curvas_vista.es_tasa_ruido`, que se promovió de privada a pública para
  que exista una sola vez y no dos copias que puedan divergir);
- los bonos **sin flujo**, que ya los reporta el detector 2 — contarlos dos veces
  infla la lista y hace parecer que hay dos problemas donde hay uno.

**Y una que importa más de lo que parece:** si la query de `portafolio.assets`
falla, la regla `sin_espejo_en_assets` **no corre** en vez de marcar los 222 bonos
como huérfanos. *"No pude mirar" nunca puede convertirse en "no está"* — es la
misma regla que hace que el job avise cuando el censo de 1816 vuelve vacío.

**Todavía SIN cron, a propósito.** Automatizar un detector antes de saber su tasa
de falsos positivos es programar ruido diario. La entrada en `deploy/crontab.txt`
se agrega cuando la calibración lo justifique.

### E1.b — PRIMERA CALIBRACIÓN contra prod (2026-08-16)

Corrida real en el Droplet: `--dry-run --detalle`, alcance `soberanos`, **29
créditos** (311/100.000 del día). Universo de 1816: **887** · nuestro master:
**221** · **107 hallazgos**.

| Regla | Salieron | Veredicto del user | Qué pasó |
|---|---:|---|---|
| `no_esta_en_curvas` | 33 | **parcial** | 6 Globales en **EUR** que no operamos + 3 **patas** `@` |
| `sin_espejo_en_assets` | 25 | **ruido** | ONs que la casa no tiene en cartera |
| `paridad_fuera_de_rango` | 16 | ✅ **real** | el patrón de la falla #4, ver abajo |
| `flujos_vacios` | 13 | **11 de ruido** | LECAP/BONCAP zero-coupon |
| `sin_tea_con_precio` | 10 | ✅ **real** | los mismos bonos que la paridad |
| `sin_ejes` | 9 | ✅ **real** | coinciden 9/9 con los documentados en RENTA_FIJA |
| `tea_fuera_de_rango` | 1 | ✅ **real** | PMA28, −38,8% |

**Lo que la calibración enseñó, que es más valioso que los números:**

**1. Los tres falsos positivos tenían la misma forma: yo reescribí un criterio
que el sistema ya tenía.**
- `flujos_vacios` miraba `bool(doc['flujos'])` cuando el predicado correcto
  —`acreencias.tiene_flujo_def`, que contempla `flujo_vencimiento`— ya existía en
  el conciliador de Manager. Una LECAP es zero-coupon: **no le falta nada**, la
  valúa `engines/curvas.py` con `tea = (flujo_vto/precio)^(365/días) − 1`.
- `sin_espejo_en_assets` no acotaba a lo que la casa TIENE, cuando el conciliador
  ya parte del último AuM. Un bono que no está en cartera no aporta al AuM: que
  no tenga asset no le falta a nadie.
- Las **patas** `@` eran el **riesgo #3 de este mismo doc**, escrito antes de
  codear y no aplicado al escribir el detector.

  → Ese es el patrón a vigilar en TODAS las etapas: *antes de escribir un
  predicado, buscar si el sistema ya lo tiene*. Ya había pasado con
  `es_tasa_ruido` y volvió a pasar tres veces en una sola corrida. La versión
  nueva siempre parece más simple porque le faltan los casos que la vieja
  aprendió a los golpes.

**2. Lo que se puede expresar como REGLA no va como lista.** Los 6 Globales en
EUR salen por `MONEDAS_SEGUIDAS`, no anotando seis tickers: una regla estructural
sigue valiendo cuando emitan el séptimo. Para lo que sí es caso por caso está
`mercado.av_agent_ignorados` (con **motivo obligatorio** — dentro de seis meses,
*por qué* se ignoró es la única pregunta que importa, y es lo que E7 va a usar
para dejar de proponerlo).

**3. El detector de tasas ACERTÓ, y el patrón es nítido.** Ocho bonos con paridad
entre **103.600% y 167.830%** (LOC6O, PECNO, PFC3O, PN40O, RC1CO, TLCDO, VSCQO,
YMCTO): ese número **es el precio en pesos sin dividir** — la falla #4 del
catálogo, un hard-dollar apuntando a la pata en pesos. Y cuatro con paridad
**0,0-0,1%** (DHSGO, OLC3O, PECKO, RZBAO): el inverso, precio USD contra flujo en
nominales. `YMCTO` es el caso testigo que ya estaba documentado a mano en
`SALUD_CURVAS` §6 — el agente lo encontró solo, junto con otros once que nadie
sabía que estaban ahí.

**4. `sin_ejes` dio 9/9 exactos** contra la lista de `RENTA_FIJA.md` paso 14
(los 10 documentados menos RMJ28, que sí tiene ejes). Es la validación cruzada
más limpia que apareció: dos caminos independientes, el mismo conjunto.

**Correcciones aplicadas** (mismo commit): predicado de flujo unificado, patas
`@` excluidas, `MONEDAS_SEGUIDAS`, `sin_espejo_en_assets` acotado a cartera,
tabla de ignorados, y **6 tests nuevos que congelan cada falso positivo** para
que no vuelva. De yapa se unificó una TERCERA copia del parser de unidad
(`ons.py` tenía el mismo regex que `acreencias`).

**Pendiente de la calibración:** decidir cuáles de los ~24 faltantes que quedan
son altas de verdad (BOPREALes nuevos, CER nuevos) y cuáles van a ignorados
(bonos viejos/ilíquidos tipo CUAP, DIP0, PAP0, PR17). Esa lista es **decisión del
user**, no del agente — y para eso existe E1.c.

### E1.c — EL AGENTE PREGUNTA (2026-08-16)

**Pedido del user, y es un cambio de forma, no una feature:** *"estaría bueno que
así como vos me decís esto, el agente pueda hacerme preguntas y en función de mi
respuesta avance. No tengo las respuestas a todo ahora mismo, pero hay que
arrancar."*

Hasta acá el agente tiraba listas y las decisiones se resolvían en un chat: cada
duda frenaba el proyecto y la respuesta se perdía. Ahora **el agente convierte la
duda en una pregunta, la deja anotada y sigue con lo que sí puede hacer**. El user
contesta cuando puede — no cuando el agente corre. En el plan de estudios esto es
*«solicitud de guía e inputs del usuario»* (módulo 03).

| Pieza | Qué es |
|---|---|
| `mercado.av_agent_preguntas` | `clave` ÚNICA (idempotencia), pregunta, opciones, contexto congelado, respuesta, nota, `aplicada_at` |
| `api/services/av_agent_preguntas.py` | generación desde hallazgos, respuesta + **efecto**, parser del comando |
| `jobs/av_agent.py --preguntas / --responder` | el canal, sin UI todavía (la bandeja es E5) |

**Cuatro decisiones de diseño que la hacen funcionar:**

1. **No repregunta.** `clave` única y estable (`falta:TZXD8`) + `ON CONFLICT DO
   NOTHING`. Una herramienta que pregunta lo mismo todas las noches se deja de
   leer, igual que una lista que repite lo descartado.
2. **Responder DISPARA un efecto, no anota una opinión.** `ignorar` escribe en
   `av_agent_ignorados` y ese ticker no vuelve a salir. Por eso `aplicada_at` es
   distinto de `respondida_at`: una respuesta cuyo efecto falló no puede quedar
   como si hubiera surtido. `alta` y `despues` guardan sin aplicar — dar de alta
   necesita bajar el cuadro y simular la TEA, que es **E2**, y decir que se aplicó
   algo que todavía no se puede hacer sería peor que decir que no.
3. **Solo pregunta lo que es decisión del NEGOCIO.** *"¿Este bono nuevo nos
   interesa?"* no lo contesta ninguna regla. *"¿Por qué este bono tiene paridad
   150.000%?"* **no se pregunta**: es el trabajo del agente (E4), y mandársela
   sería delegarle al user justo el laburo que el agente vino a hacer.
4. **`despues` es una respuesta legítima.** Sin ella, la única forma de no decidir
   es no contestar, y entonces no se distingue *"lo pensé y lo dejo para después"*
   de *"no lo vi"*.

**El comando acepta RANGOS** (`"3=alta,5-9=ignorar"`) porque la forma real de
contestar 24 preguntas es *"estas dos sí, el resto no"*, y obligar a tipear 24
asignaciones en la consola web del Droplet garantiza que no se conteste nunca
(REGLA #0). El parser **grita ante cualquier ambigüedad** en vez de interpretar:
`ignorar` es silencioso y permanente, así que adivinar ahí significa ignorar un
bono que se quería dar de alta, y eso no se descubre nunca.

**Las 3 decisiones abiertas (§4) ahora también son preguntas del agente**
(`DECISIONES_ABIERTAS`), porque una decisión que solo vive en un markdown depende
de que alguien lo lea. Su efecto no es automático: lo aplica la etapa que
corresponda, pero la respuesta queda asentada donde el agente la va a buscar.

**Las preguntas se registran incluso en `--dry-run`**: no son un resultado del
relevamiento sino una conversación pendiente, y perderlas porque la corrida fue de
prueba obligaría a pagar el censo de nuevo para recuperarlas.

### E2 — El simulador
Calcular la TEA/paridad/duration que **tendría** un bono con un flujo dado, sin
escribir nada. Es la pieza que convierte "dar de alta un bono" de un acto de fe en
una decisión informada — hoy no se sabe cómo va a quedar hasta reiniciar los
motores y mirar el chart.

Reusa `debug_curva.py` (que ya replica el motor), `calcular_campos` y
`rama_calculo`.

**Validación — el test más honesto que hay:** correrlo sobre los bonos que **ya
tienen** TEA persistida y verificar que reproduce el número exacto. Si no puede
reproducir lo conocido, no puede predecir lo desconocido. Precedente que dice que
la técnica funciona: `diag_convertir_flujos` del paso 15 logró **Δ = +0 bps** en 7
bonos.

### E3 — El set de control
`evals/av_agent.json` con los casos que **hoy existen y tienen respuesta conocida**:

- los **10 bonos sin ejes** (BA37, BB37, SA24, SF27, RMJ28, NZC30, IR2PO, PN430,
  VSCWO, Y134O);
- los **9 sin TEA con el OK de la mesa** (CP36O, MGCOO, VSCYO, VSCZO, PMA28,
  CO3D7, TMF27, CO2D7, RMJ28);
- **AER9O** con su divergencia de 3,1% medida;
- **AFCHO / CS450 / HBCAO** como casos de *tasa que parece rota y no lo está*
  (duration ultra-corta → `tasa_ruido`): el set tiene que castigar los falsos
  positivos igual que los falsos negativos;
- las **7 fallas** del catálogo de `SALUD_CURVAS` §6.

**Validación:** el set corre contra los detectores de E1 y da el número base.

### E4 — El cerebro (primer uso de DeepSeek en este agente)
Tarea nueva en `core/ai.py`: **`av_agent_diagnostico`**, tier **pro** con
**thinking enabled** — mismo criterio que `triage_incidente` y
`salud_diagnostico`, porque es diagnóstico y no resumen, y `max_tokens` alto
(lección del P2: el razonamiento cuenta como output y con poco vuelve vacía).

Entrada: un bono sospechoso + su evidencia ya recolectada por E1/E2. Salida:
clasificación contra el catálogo de fallas + causa raíz + confianza **con
motivo**. Datos públicos de mercado → **DeepSeek, sin aduana** (no hay dato de
negocio ni identidad en juego).

**Degradación (regla de oro 4):** si el LLM no responde, los hallazgos de E1 y la
simulación de E2 salen igual, sin la narrativa. Lo que salva el trabajo no depende
del modelo.

**Validación: contra E3, no contra la impresión.** Un número (X/25) que se puede
comparar entre versiones del prompt.

### E5 — La bandeja
Manager → BONOS revivido (`/bonos`, `/bonos/sin-flujo`, `/bonos/sin-tasa` ya
existen y están sin uso). Cada propuesta muestra, en este orden: **la frase en
castellano** · **la evidencia lado a lado** · **la simulación de E2** · **la
confianza con su motivo** · **aplicar / rechazar-con-motivo**.

El motivo del rechazo **no es burocracia: es el combustible de E7.**

**Autonomía: CERO.** Propone todo, aplica nada. Se junta la estadística que
habilita E6.

### E6 — La primera lane automática
**Solo** "completar flujo faltante donde hoy no hay ninguno". Es la única acción
con riesgo asimétrico: ese bono hoy no tiene TEA, no está en el gráfico y no entra
al fair value — pasar de nada a algo no puede empeorar un número existente. Con
`before` guardado y **botón de revertir**.

> **La autonomía se GANA con estadística, no se configura con fe.** Se otorga por
> lane y por dominio (una curva donde nunca erró), se mide, y **se retira sola** si
> la lane empieza a fallar. La reversibilidad es lo que la hace barata.

### E7 — Aprendizaje y digest
Los rechazos con motivo vuelven al prompt como ejemplos (aprendizaje **no
paramétrico** — cero fine-tuning). Digest semanal con el número del set de
control, que es la señal temprana de degradación.

---

## 6. Cómo se entera el user (diseño de la experiencia)

**Por default, no se entera de nada.** El 90% del trabajo de un agente de
integridad es confirmar que todo está bien, y eso no es noticia. **El silencio ES
el reporte.** Un agente que avisa todos los días entrena a que lo ignoren.

| Canal | Cuándo | Qué |
|---|---|---|
| **Un contador** en el nav de Manager | siempre | "3 propuestas". No interrumpe, espera. |
| **El modal de SALUD** (ya existe) | solo si algo se rompió HOY | Un bono que ayer tenía TEA y hoy no. Reusa el criterio ya construido: solo transición nueva a problema y sin ver — nunca cuando algo se arregla. |
| **Digest semanal** | lunes | 4 líneas: detectados / aprobados / rechazados / **set de control X/25**. |

**Failing gracefully.** 1816 caído → el agente **no corre y lo dice**; nunca
confunde *"no encontré nada"* con *"no pude mirar"* (esa confusión es cómo un
monitoreo miente en verde). LLM caído → los pasos deterministas corren igual.
Aplicó algo mal → está en el historial con su `before` y se revierte con un click.

---

## 7. Riesgos conocidos

1. **Un flujo mal escalado rompe el chart entero** (escala del eje) y contamina
   el AuM vía el join a `portafolio.assets`. Es el riesgo #1 y la razón de que E2
   (simulación) venga antes que cualquier escritura.
2. **Los motores cargan `mercado.curvas` UNA sola vez al arrancar** — un alta no
   impacta hasta reiniciar `motor_rofex` + `motor_curvas` (`SALUD_CURVAS` §3). El
   agente tiene que decirlo en la propuesta o el user va a creer que no funcionó.
3. **Las patas de los duales NO tienen cuadro propio**: medido, `TTS26 @TASA FIJA`
   da 404 en `/cashflow` aunque figure en `/instrumentos`. Son vistas de valuación
   por componente — hay que pedir el ticker base. Un agente que no sepa esto va a
   reportar errores falsos todos los días.
4. **Falsos positivos en las tasas.** Es el riesgo de producto: si la lista de E1
   trae ruido, nadie la mira a las tres semanas. Por eso E3 castiga el falso
   positivo igual que el falso negativo.
5. **La divergencia de AER9O sigue sin explicar.** Automatizar sobre una
   divergencia que no se entendió es propagar el error más rápido.

---

### E1.d — LA VISTA `/av-agent` (2026-08-16)

**Pedido del user:** *"¿no podemos ponerle una interfaz? Hoy tengo el chat
CONSULTALE A LA IA que literal no se usa para nada."* Un agente que solo vive en
la consola del Droplet no lo usa nadie más que quien tiene SSH.

| Pieza | Qué es |
|---|---|
| `GET /api/ia/av-agent/vista` | toda la pantalla en UN request (`api/services/av_agent_vista.py`) |
| `POST /api/ia/av-agent/responder` | contesta y aplica el efecto |
| `POST /api/ia/av-agent/designorar` | deshace un «no me interesa» |
| `src/components/av-agent-modal.tsx` | botón en la **barra inferior** + modal |

**No es una vista del nav: es un botón en la barra de estado + un MODAL**
(decisión del user 2026-08-16, tras ver la primera versión como vista). El
razonamiento vale para cualquier agente que venga después: *el AV Agent no es una
vista de datos que se consulta, es un canal que INTERRUMPE cuando tiene algo que
preguntar.* Una entrada en el nav compite con RENTA FIJA y TRADING —pantallas que
se abren para trabajar— y pierde, porque **nadie navega a un agente**. En la barra
inferior, al lado de BRIEFING y SALUD, está siempre presente, no ocupa lugar hasta
que se abre, y su **contador de preguntas se ve desde cualquier pantalla**. La
vista `/av-agent` y su página se BORRARON: dos caminos a lo mismo es superficie
que mantener sin nada a cambio.

**Vive bajo `/api/ia` a propósito**: hereda el gate de forma ESTRUCTURAL en vez de
estrenar un prefijo que habría que acordarse de sumar a
`ENDPOINT_MODULE_PREFIXES`. Un endpoint de IA fuera de ese prefijo nace sin gate y
eso no se ve hasta que alguien lo prueba sin permisos.

**TODO admin-only**, apretado desde el módulo `ia` (decisión del user): el agente
expone el estado interno de la valuación —qué bonos están mal cargados, cuáles no
entran al AuM, qué le falta al catálogo—. Eso no es información de mercado: es
cómo está hecho el sistema por dentro. Y como la matriz de roles le da `ia` a la
mesa para los copilotos, gatearlo solo por módulo se lo mostraría a un comercial.
Mismo criterio que SALUD, y **decidido también en el server**: sin `manager` el
componente no existe en el HTML, no pollea y no puede mostrar nada.

**Tres decisiones de la pantalla, y ninguna es cosmética:**

1. **Las preguntas van primero y son el tab default.** Los hallazgos son
   informativos; las preguntas son lo único que el agente NO puede resolver
   solo. Una pantalla que abre en la lista de 64 problemas deja las 27 preguntas
   abajo y sin contestar — y sin respuestas el agente no aprende nada.
2. **El agente habla en primera persona.** La diferencia entre *"hallazgos: 24"*
   y *"encontré 24 bonos que no tenés, ¿cuáles te interesan?"* es si el usuario
   entiende que le toca hacer algo. Un tablero no se contesta; una pregunta sí.
3. **Dice lo que NO puede hacer.** `capacidades.puede_dar_de_alta` viaja en la
   respuesta: hoy contestar «alta» GUARDA la decisión pero no da de alta nada
   (eso es E2), y el historial marca `guardado, todavía sin aplicar`. Sin decirlo,
   el botón se lee como roto.

**El botón DESHACER no es un extra.** Si `ignorar` fuera irreversible desde la
app, la respuesta segura pasaría a ser *no contestar nada* y el canal entero
dejaría de usarse. Deshacer saca el ticker de la lista **y reabre su pregunta**:
sin lo segundo, el bono volvería a salir como hallazgo pero sin nada que
contestar.

> ⚠️ **Bug atajado antes de prod:** el endpoint de deshacer nació como `DELETE` y
> el proxy catch-all de Next para `/api/ia` **solo expone GET y POST** → habría
> dado 405 en producción con el código compilando perfecto. Se pasó a POST en vez
> de agregarle DELETE al catch-all: ese verbo quedaría habilitado para TODOS los
> endpoints de `/api/ia`, presentes y futuros, a cambio de la elegancia REST de
> uno solo. **La superficie mínima gana.**

### E1.e — CONTEXTO en las preguntas (2026-08-16)

**Pedido del user usándolo de verdad:** *"hay bonos que no los conozco y
necesitaría al menos el emisor"*. Tenía razón: **un ticker solo no es una
pregunta contestable.** `M31G6` no le dice nada a nadie, y una pregunta que no se
puede contestar es una pregunta que no se contesta.

La ficha sale del **mismo crédito** que ya se paga: el censo de `/instrumentos`
trae `emisorNombre`, `denominacion`, `monedaDenom`, `fechaEmision` e `isinCode` en
cada instrumento (nombres verificados contra `jobs/mercado_1816_discovery`, que
persiste ese mismo catálogo). No costó una llamada más — estaban ahí y no se
mostraban.

**Y se agregó algo que vale más que el emisor: «¿LA CASA YA LO TIENE?»** Se cruza
el faltante contra la tenencia del último AuM. Un bono que está en la cartera y
**no** está en `mercado.curvas` **no valúa**: no tiene TEA, no entra al gráfico y
su posición se muestra sin precio modelado. Ahí *"¿te interesa?"* deja de ser una
preferencia y pasa a ser **un arreglo pendiente** — por eso esos suben a
severidad `alta`, la tarjeta va con borde rojo y **encabezan la lista**. Marcar
algo como urgente y dejarlo en la tarjeta 18 es lo mismo que no marcarlo.

**`registrar()` pasó de `DO NOTHING` a `DO UPDATE … WHERE estado = 'abierta'`.**
Sin eso, las 21 preguntas que ya estaban abiertas se quedaban con el texto viejo
para siempre y había que borrarlas a mano para verlas bien. Las dos reglas
conviven: *no se repregunta* (la clave sigue siendo única) pero *sí se mejora el
enunciado*. El `WHERE` no se puede saltear — **una pregunta ya respondida se
congela con el texto y el contexto que tenía cuando se contestó**; reescribirla
haría que el historial diga que se decidió sobre una evidencia que en ese momento
no existía.

### E1.f — el agente ve los HUECOS DEL SISTEMA (2026-08-16)

**Lo encontró el user usándolo**, y es el hallazgo de producto más importante
hasta acá: estaba dando de alta bonos BADLAR con la nota *"lo doy de alta pero
actualmente no tenemos curva BADLAR (y hay que agregar)"*.

**Verificado en el código** (`core/curvas_ejes.py::_pill_de_ajuste`, última
línea): `return None  # badlar / tpm / caucion todavía no tienen pill`. O sea:
1816 publica «Soberanos ARS Badlar», nuestros ejes aceptan `ajuste='badlar'`…
**y esos bonos no caen en ninguna pill, por lo tanto en ninguna curva**.
`por_curva()` y `sql_universo()` no los encuentran: quedan cargados y **no
aparecen en la tabla, ni en los forwards, ni en el fair value, sin dar un solo
error**. Es el mismo modo de falla del paso 14 de `RENTA_FIJA.md` — nada se
rompe, el bono simplemente no está.

**Es un hallazgo de otra naturaleza que los tres anteriores.** Los otros son
datos mal cargados; este es **una capacidad que le falta al sistema**. Por eso:

- tipo propio (`hueco_de_curva`) y se reporta **una vez por AJUSTE**, no por
  bono — el problema es el ajuste, los bonos son la evidencia de cuánto duele;
- va **primero** en la lista: arreglar el dato de un bono que igual no se ve es
  trabajo perdido;
- y la **pregunta de alta lo avisa ANTES** (`ajuste_sin_curva` en la evidencia →
  chip «BADLAR SIN CURVA» en la tarjeta). Eso es exactamente lo que el user
  estaba escribiendo a mano en la nota de cada respuesta.

`curvas_ejes.ajuste_sin_curva()` **se deriva de `_pill_de_ajuste`**, no es una
lista escrita a mano: el día que `badlar` tenga su pill, el aviso desaparece
solo. Una lista paralela seguiría diciendo que falta cuando ya no falta — la
clase de aviso que enseña a ignorar los avisos.

**Lo que el agente NO va a hacer, y no es una limitación técnica:** darle una
pill a `badlar` es tocar `curvas_ejes` + `sql_universo` + la vista del front. Es
**desarrollo, no dato**. El código que decide en qué tabla aparece cada bono no
puede cambiarlo un proceso automático de noche: un error ahí mueve bonos de tabla
en silencio, que es literalmente el bug de los pasos 14 y 16. El agente ve el
hueco, lo mide y lo dice; construirlo es una decisión humana.

### E1.g — dos bugs de la corrida real (2026-08-16)

**1. El job contaba un hallazgo que no imprimía.** Total 59, bloques 18+2+38=58.
La lista de tipos a mostrar estaba **escrita a mano** y `hueco_de_curva` —el
detector recién agregado— no estaba. Ahora los tipos se DERIVAN de lo que vino, y
un tipo sin etiqueta se imprime igual con su nombre crudo: **preferimos una fila
fea a una fila que falta**. *Un hallazgo que el reporte no imprime es un hallazgo
que no existe* — justo el modo de falla que ese detector vino a denunciar. Test
que congela el invariante.

**2. El diag concluyó sobre n=1.** `diag_curva_nueva` sondeó `badlar` con UN solo
ticker (RMJ28, el único que ya estaba en `mercado.curvas`) y dictaminó "1816 no
publica su tasa". **Con n=1, y encima un provincial ilíquido, eso no es una
medición: es una anécdota.** Los BADLAR que importan (TB27, TB31P, TD26) todavía
no están en nuestra base — el universo a sondear es el de 1816, no el nuestro.
Ahora suma los tickers del catálogo `research.mkt_1816_instrumentos` (**0
créditos**, ya está persistido) y **avisa explícitamente cuando la muestra es
menor a 3**.

**3. Y el tab de preguntas vacío mentía.** Con las 27 contestadas decía "no tengo
nada que preguntarte" al lado de 38 hallazgos de severidad alta. *"No tengo
preguntas" no es "no pasa nada"*: ahora el vacío dice cuántas cosas urgentes hay
y linkea al tab que las tiene.

### E1.h — el agente CREA la curva (2026-08-17)

**Pedido del user, en sus palabras:** *"si el bono soberano lo detecta en una
curva que no había, que la agregue. Y que me pregunte si eso se valúa con 1816
(como los TAMAR, y que lo agregue al job) o si se valúa por nosotros."*

**Por qué se podía.** `curvas_ejes._pill_de_ajuste` era una función con ocho `if`
que devolvían un string: **una tabla disfrazada de código**. Mientras lo fue,
agregar una curva era un deploy — y por eso `badlar`, `tpm` y `caucion` nunca la
tuvieron. Crear una curva son cinco datos (`pill`, `display`, `lado`, `orden`,
predicado SQL) y ninguno es matemática.

| Pieza | Qué es |
|---|---|
| `mercado.curvas_catalogo` | la curva como DATO: ajuste, pill, display, lado, orden, **fuente_valuacion** |
| `core/curvas_catalogo.py` | lectura cacheada (TTL 60s) + `crear()` validado |
| `curvas_ejes` | `_pill_de_ajuste` consulta el catálogo; `pills_disponibles()`, `display_de()`, `lado_de()`; `sql_universo` GENERA el predicado |
| `curvas_vista` | arma la barra de pills con `pills_disponibles()` → la curva nueva aparece **sin tocar el front** |
| pregunta `curva:<ajuste>` | responder `1816` o `motor` **crea la curva** |

**La pregunta no es «¿creo la curva?»** — eso ya lo decidió el hecho de que hay
bonos invisibles. Lo único que falta es **de dónde sale la tasa**, así que se
pregunta eso y la respuesta la crea. Un sí/no seguido de un cómo son dos clicks
para una sola decisión. Y **el lado (ARS/USD) no se pregunta**: lo dice la moneda
de sus propios bonos — preguntar algo que el dato ya contesta hace perder tiempo
y abre la puerta a contestarlo distinto de la realidad.

**Las dos respuestas no son simétricas, y por eso son dos:**
- **`1816`** → se trae, igual que los TAMAR. Es configuración pura: **sin código**.
- **`motor`** → la curva se crea igual y los bonos ya aparecen con precio y
  duration, pero la TEA llega cuando alguien escriba la rama de cálculo. **La
  matemática no la escribe un agente.**

**Tres cosas que hacen que esto no pueda romper nada:**

1. **El catálogo SUMA, nunca PISA.** `crear()` rechaza un ajuste que ya tenga
   pill en código. Un catálogo que puede redefinir una curva existente es un
   catálogo que puede mover 129 bonos de tabla en silencio.
2. **Si la tabla no responde, todo funciona como antes.** El import es lazy y
   degrada a `None`/`{}`; las 5 curvas viejas siguen en código a propósito.
3. **El predicado SQL se GENERA, no se guarda como texto.** Un `WHERE` escrito a
   mano en una fila de configuración es una inyección esperando y, peor, un
   criterio que puede contradecir a `pills()` sin que nadie lo note.

> **Descartado: `scripts/diag_curva_nueva`** (vivió 40 minutos). Se escribió para
> medir si 1816 podía valuar BADLAR — pero eso **no era una duda**: 1816 publica
> TEA y `spread` para BADLAR de la misma forma que para TAMAR, y el job de TAMAR
> ya es la prueba. El diag además muestreó los 25 primeros ALFABÉTICOS de las tres
> curvas Badlar (casi todos provinciales ilíquidos) y dejó afuera justo los
> soberanos que importaban. **Lección: medir es la regla, pero medir lo que ya
> está probado es procrastinar con forma de rigor.**

### E1.i — el LIBRO DE ACCIONES (2026-08-17)

**Pedido del user:** *"me gustaría que haya una tab donde se vean las acciones
realizadas, todo bien trazable: hora, fecha, qué base tocó, qué agregó."*

**Y es obligatorio, no un lujo.** Hasta acá esa información existía pero
DESPARRAMADA: `av_agent_preguntas.aplicada_at` decía cuándo surtió una respuesta,
`av_agent_ignorados.creado_at` cuándo se ignoró un ticker,
`curvas_catalogo.creada_at` cuándo nació una curva. Ninguna contesta la pregunta
completa, y para reconstruirla hay que cruzar tres tablas sabiendo de antemano qué
buscar. **En cuanto el agente escriba en `mercado.curvas` (E2), una escritura
automática sin libro es una escritura que nadie puede auditar ni revertir** — y el
momento de construirlo es ANTES de esa etapa, no después del primer susto.

`mercado.av_agent_acciones` (append-only) + `api/services/av_agent_acciones.py` +
la tab **HIZO** del modal: cuándo (hora ART), qué hizo, sobre qué, **en qué tabla
escribió**, quién lo pidió, de qué pregunta salió, y el detalle.

**Tres decisiones que lo hacen servir:**

1. **Se anotan también los FALLOS** (`ok=false` + `error`). Un libro que solo
   registra los éxitos hace parecer que el agente nunca se equivoca, y esconde
   justo el caso que uno va a querer investigar.
2. **`antes` guarda el estado previo.** Sin eso, "revertir" es una promesa y no
   una función. El `designorar` ya lo usa: lee el motivo antes de borrarlo.
3. **Registrar NUNCA rompe la acción.** Si el libro falla, la escritura real ya
   pasó y no se deshace por un problema de auditoría. El orden inverso —fallar la
   acción porque no se pudo anotar— dejaría al usuario sin la función *y* sin el
   registro.

**El nombre de la tabla se muestra en crudo** (`mercado.curvas_catalogo`, no "el
catálogo de curvas"): el libro se lee para ir a mirar esa tabla, y traducirlo a
lenguaje humano lo haría inservible justo para eso. Y la hora es **ART**, no la
del servidor: se lee para reconstruir qué pasó a tal hora, y esa hora es la del
que operó.

### E2 — SIMULAR y APLICAR el alta (2026-08-17)

Contestar «alta» guardaba la decisión y nada más. Ahora la ejecuta:
`api/services/av_agent_alta.py` baja el cuadro de 1816, lo convierte a NUESTRA
shape, **calcula la TEA que TENDRÍA el bono sin escribir nada**, y recién con ese
número a la vista se aplica. Todo desde el modal: botones **SIMULAR** y
**APLICAR** en la misma fila del hallazgo (`ENCONTRÓ` deja de ser solo lectura).

**El simulador ES el guardrail.** Un flujo mal escalado no da error: da una TEA
absurda o ninguna. Si se escribiera igual rompería la escala del chart y
contaminaría el AuM vía el join con `portafolio.assets`. Simulando primero, ese
error se ve ANTES y el alta no se aplica.

**Alcance deliberadamente acotado — qué se da de alta solo y qué no:**

| Rama | ¿Alta automática? | Por qué |
|---|---|---|
| `tasa_fija` bullet | **sí** | un pago: `flujo_vencimiento` |
| `tasa_fija` con cupón | **sí** | `amortizacion` + `interes` = lo que manda 1816 |
| `soberanos` | **sí** | acá `cupon_sobre_residual` **es un monto por 100**, igual que 1816 |
| `cer` | **no** | acá el MISMO campo es una **TASA** sobre el residual vivo, y exige `cer_emision` |
| `tamar` / `dual` | **no** | shape propia y valuación por otro riel |

**Esa diferencia de significado es la trampa del paso 15 de `RENTA_FIJA.md`**: la
primera conversión que alguien escribió estaba mal por eso (un cupón de 2 daba
200) y **no se veía leyendo el código** — la cazó un chequeo numérico. Las ramas
donde un campo significa dos cosas se simulan y se muestran, pero las carga un
humano.

Otras tres cosas que no son detalle: la **escala se MIDE** (Σ amortizaciones ≈
100 → VN 100; si no, nominales) porque no hay un divisor global; se usa la **fecha
EFECTIVA** (por teórica matchean 13/23 cupones, por efectiva 21/23); y el alta
escribe por **`bonos_admin.upsert_bono`**, la misma puerta que usa la mesa desde
Manager — así no puede existir un alta del agente con otra shape que una humana.

Cada aplicación queda en el **libro** (`alta_bono`) con la TEA simulada, y la
respuesta avisa que **los motores cargan `mercado.curvas` al arrancar**: la tasa
aparece recién tras reiniciar `motor_rofex` + `motor_curvas`.

### E2.b — CALIBRACIÓN de la simulación (2026-08-17)

Tres cosas mal, encontradas por el user simulando de verdad.

**1. El motivo de «no aplicable» era un texto FIJO.** Decía *«en CER
`cupon_sobre_residual` es una TASA»* hasta para un BADLAR y para un dólar-linked,
que no tienen nada que ver con CER. **Un mensaje que no habla del caso que uno
está mirando no explica: confunde.** Ahora hay un motivo por rama — y el de una
curva creada con `fuente_valuacion='1816'` dice lo que corresponde: *"su tasa se
trae de 1816, acá no hay nada que simular; el cuadro alcanza para darlo de alta"*.

**2. CER pasa a ser ALTA AUTOMÁTICA: el `cer_emision` se INFIERE.** No hacía falta
que 1816 lo mandara — su catálogo trae `fechaEmision` (ya persistido en
`research.mkt_1816_instrumentos`, **0 créditos**) y la serie CER es nuestra. Se
calcula con `get_cer_liquidacion`, **la misma función que usa el motor**, con su
T−10 hábiles: calcularlo distinto dejaría al bono con un divisor que no es el de
la valuación, y la TEA saldría corrida sin que nada falle.

Y con eso se resolvió la conversión que faltaba: en la rama `cer`,
`cupon_sobre_residual` **es la TASA sobre el residual vivo**, no el monto. El
conversor ahora deriva el residual pago a pago y expresa el cupón como tasa
(`2,0` sobre 100 vivos y `1,0` sobre 50 son la MISMA tasa del 2%). Hay test
numérico — es la única forma de cazar esto, porque leyendo el código no se ve.

**3. Faltaba la pregunta más importante: ¿VA A TENER PRECIO?** Dar de alta no
alcanza. La cadena real es:

> **símbolo en Primary → el motor lo suscribe (lee `mercado.curvas` AL ARRANCAR) →
> llega el trade → `mercado.market_snapshot` → `motor_curvas` calcula la TEA**

Si el símbolo no está en el catálogo de Primary, **el bono queda dado de alta,
sin precio y sin TEA, para siempre, y nadie sabe por qué**. Ahora el simulador lo
chequea contra `core/instrumentos_validos` (la misma fuente que filtra las
suscripciones de todos los motores) y lo dice antes de aplicar. `None` = no se
pudo leer el catálogo → no se afirma nada.

### E2.c — EL PRE-FLIGHT: la cadena completa, paso por paso (2026-08-17)

> *«¿QUÉ PASA SI HAGO APLICAR? SABÉS SI EL PRECIO, SI LA ESPECIE ESTÁ YA, SI
> PODRÍA SUSCRIBIRSE NORMALMENTE EN EL MOTOR, E IR A MARKET SNAPSHOT. TIENE QUE
> PASAR TODO EL CHEQUEO, EL PASO A PASO, Y VALIDAR QUE PUEDE LLEGAR — **COMO SI
> LO HARÍA YO MISMO**.»* — el user, 2026-08-17.

**El problema.** E2.b chequeaba UN eslabón (¿Primary lista el símbolo?) y solo
avisaba **al fallar**. Eso deja dos agujeros: los otros seis eslabones no se
miran, y ver la pantalla sin advertencias no distingue *"todo bien"* de *"no
chequeé"*. Y el modo de fallar de un alta **no es una excepción**: es un bono
escrito que nunca recibe precio, cuyo síntoma es una celda vacía tres días
después. Aplicar sin ver la cadena es firmar a ciegas.

**Los ocho eslabones**, cada uno con `ok` / `falla` / `atencion` /
`no_se_puede_saber`, la tabla real que toca y —cuando hay algo que hacer— la
acción concreta:

| # | Paso | Rompe en silencio si… |
|---|---|---|
| 1 | la curva de 1816 traduce a nuestros ejes | el bono se clasifica mal |
| 2 | 1816 mandó el cuadro, con escala reconocible | viene en NOMINALES y la paridad sale ×50 |
| 3 | la rama sabe convertirlo sin ambigüedad | `cupon_sobre_residual` significa dos cosas distintas |
| 4 | (CER) hay `cer_emision` | el motor solo devuelve duration |
| 5 | **el papel TIENE especie** (`mercado.especies`) | el símbolo era una adivinanza |
| 6 | Primary lista ese símbolo | `core/websocket` filtra la suscripción → nunca hay precio |
| 7 | el motor lo suscribe → `market_snapshot` | los motores leen `mercado.curvas` **al arrancar** |
| 8 | hay precio hoy / la TEA se calcula / hay espejo en `assets` | la celda queda vacía, o el bono no entra al AuM |

**Tres decisiones que valen más que la lista:**

1. **Se devuelven TODOS los pasos, también los verdes.** Mostrar solo lo que
   falla obliga al que mira a confiar en que el resto se chequeó — que es
   exactamente lo que este cuadro vino a reemplazar.
2. **El paso 7 NUNCA es verde solo.** Es una acción manual (reiniciar
   `motor_rofex` + `motor_curvas`) y decirlo es la mitad del valor del
   pre-flight: el alta puede estar perfecta y el bono seguir sin precio hasta el
   próximo restart.
3. **`no_se_puede_saber` es un estado de primera clase.** Si Postgres no
   responde, *"no pude mirar"* no es *"no está"*: los pasos de la cadena de
   precio salen en ese estado y el veredicto lo dice. REGLA #2 aplicada al
   propio agente.

**Lo que cambió además, y no es cosmético:**

- **El símbolo deja de adivinarse.** Venía armado como `MERV - XMEV - {tk} -
  24hs`. Ahora sale de **`mercado.especies`** (default primero, después 24hs
  sobre CI, que es donde hay liquidez) — la MISMA fuente de la que
  `jobs/assets_autofill` deriva `assets.instrumento`. Usar otra habría creado una
  segunda verdad que se desincroniza sola. Sin fila en especies se cae al símbolo
  armado, **pero el paso 5 lo canta** en vez de disimularlo.
- **Un paso en `falla` BLOQUEA el `aplicar`.** `aplicable` mira la rama; el
  pre-flight mira la cadena, y la cadena es lo que decide si el bono va a existir
  de verdad o solo a estar escrito.
- **`aplicar` dejó de pedirle el cuadro a 1816 dos veces.** Simulaba (1 llamada) y
  volvía a pedir el cashflow para armar el payload. Esa llamada cuesta **un
  crédito POR CUPÓN**, así que un bono de 20 cupones pagaba 40. Y peor que el
  gasto: abría la puerta a aplicar un cuadro distinto del que se mostró, que es
  justo lo que el simulador previene. Ahora `simular` devuelve el cuadro ya
  convertido y `aplicar` lo reusa.
- **El libro de acciones guarda las advertencias.** Dentro de un mes, *"¿por qué
  este bono no tiene precio?"* se contesta mirando `av_agent_acciones` en vez de
  reconstruirlo.

Costo en queries: **una sola conexión, tres `execute`** (`especies`, `curvas`,
`assets`) — el peaje a Supabase es de ~8.5ms por roundtrip y lo que importa es la
cantidad, no el plan. El precio del snapshot ya se leía para simular la TEA: el
paso 8 lo reusa en vez de pedirlo de nuevo.

### E2.d — El PRECIO DE REFERENCIA de 1816, y el CONTROL CRUZADO (2026-08-17)

> *«PODEMOS USAR UN PRECIO DE REFERENCIA QUE SÍ LO PODEMOS SACAR DE 1816, EL
> `precioClean` O ALGO SIMILAR… NO ES QUE HAY QUE PERSISTIRLO POSTA, PERO PARA
> CASOS NUEVOS Y NO ESPERAR A VER SI SE ROMPE, USAMOS EL DE 1816 Y HACEMOS
> CÁLCULOS CON ESO.»* — el user, 2026-08-17.

**El agujero que tapa.** Un bono que se acaba de dar de alta **nunca** tiene
precio en `mercado.market_snapshot` — no se suscribió todavía. O sea que en el
único momento en que el simulador hace falta de verdad, no podía calcular nada:
GD46 mostraba *«sin precio no se puede simular la TEA»* y había que aplicar a
ciegas y esperar a ver si salía bien. Exactamente lo que el user quería evitar.

`precioClean` **ya estaba verificado en producción** (lo piden todos los días
`jobs/mercado_1816_series` y `jobs/tamar_1816`) — no hizo falta una prueba.

**Pero lo que vale más no es el precio: es que 1816 publica SU PROPIA TEA.**

Eso convierte la simulación en un **control cruzado**. Se corre NUESTRO motor
sobre EL PRECIO DE ELLOS y se compara contra SU tasa:

| Resultado | Qué significa |
|---|---|
| las dos coinciden | dos cálculos independientes dan lo mismo → **el cuadro está bien convertido** |
| se parecen (≤300 bps) | convención de días, o su precio es *clean* y el nuestro trae intereses corridos |
| se contradicen | con el mismo precio, casi siempre es **la escala del cuadro o la pata equivocada** |

Esto es lo que faltaba. Un cuadro mal convertido **no tira error**: da un número
plausible y equivocado, y ahí se acaban las formas de darse cuenta leyendo — es
literalmente la trampa del paso 15 de `RENTA_FIJA.md`, que se cazó con un chequeo
numérico y no revisando el código. Ahora ese chequeo numérico existe para
cualquier bono, incluido uno que jamás cotizó acá.

**Cuatro decisiones:**

1. **El precio de 1816 NO se persiste.** `market_snapshot` es del motor (Primary,
   live, 5s); esto es 1816/BYMA con delay. Mezclarlos escondería cuál es cuál —
   el mismo criterio por el que `jobs/tamar_1816` escribe en su propia tabla.
2. **Orden de precios: snapshot primero, 1816 después.** Nunca al revés. El de
   referencia sirve para poder calcular algo, no para reemplazar al real. La UI
   dice cuál se usó.
3. **La banda es un PRIMER CORTE y está declarada como tal.** La única evidencia
   dura es el TAMAR contra la planilla de la mesa: **7 bps** de diferencia cuando
   las dos partes están bien. De ahí sale el ≤50 bps de "coinciden"; el techo de
   300 es un juicio. Por eso una contradicción es `atencion` y **nunca `falla`**:
   bloquear un alta con un umbral no medido sería inventar un hecho (REGLA #2).
   Que la banda se calibre con el uso es el diseño, no una deuda.
4. **Costo: 4 créditos** (1 ticker × 4 campos) y solo al apretar SIMULAR, sobre
   100.000 diarios.

**De paso, una duplicación menos.** La lógica de *«pedir `indicadores` con
`fechaOperacion` explícita y retroceder si la rueda vino vacía»* vivía dentro de
`jobs/tamar_1816` y solo ahí — una trampa que ya se pagó dos veces (sin fecha, un
domingo devuelve todo `null` y parece que el campo no existe). Se movió a
**`core.mercado_1816.indicadores_vigentes`** y el job ahora la llama. Dos
criterios para la misma pregunta terminan siempre con uno de los dos viejo.

**Y un cambio de forma en los chequeos**: cada paso tiene `clave` estable y el
`n` se numera al final, sobre los pasos que realmente aplicaron (el de CER solo
está en la rama CER, el cotejo solo si 1816 contestó). Antes el orden ERA la
identidad, así que insertar un paso en el medio renumeraba todo.

### E2.e — El alta SIEMBRA la especie, y el agente aprende qué NO calculamos (2026-08-17)

Tres cosas que la corrida real de GD46 y TMG27 dejó a la vista.

**1. Sembrar la especie ES parte del alta, no un requisito previo.**

> *«Estaría bueno que lo haga el agente también en el paso a paso. Es parte de
> dar el alta justamente. Y tiene que quedar la pata en USD y en ARS si estuviese
> en ambas monedas. Se agrega como instancia final — porque hay que agregar algo
> que sabés que va a quedar productivo. No es puntualmente algo que no va a
> funcionar si no está creado el instrumento, es solo decir: no está listo.»*

Tenía razón y el diseño estaba mal. El paso de la especie estaba en **FALLA** con
un *«correr `sembrar_especies --aplicar` y volver a simular»* — o sea, el agente
detectaba trabajo y se lo devolvía al humano. Ahora:

- El paso de la especie es **`atencion`**, no bloquea: *«no hace falta hacer nada,
  si el resto da OK el alta la siembra sola»*.
- Se agrega un paso **ÚLTIMO** (`sembrar`), y último a propósito: es lo que el
  alta VA A HACER, no algo que falta.
- `aplicar()` lo ejecuta **después** del upsert (solo tiene sentido sembrar la
  especie de un bono que existe) y **no puede tumbar el alta** — la fila de
  `mercado.curvas` es lo que mueve la vista.
- **Deja las DOS patas**, ARS y USD, cuando Primary las lista.

Para no reescribir la clasificación de patas —el bug de la primera corrida del
seeder fue justamente ahí— la lógica pura se movió a **`core/especies.py`** y
ahora `scripts/sembrar_especies` (lote) y el agente (de a uno) usan la MISMA.

**2. Los TAMAR: el agente no sabía que NO los calculamos.**

> *«Claramente los TAMAR no se está enterando de que nosotros NO LOS CALCULAMOS.
> Solamente usamos la TEA y el spread que viene de 1816. El resto de los datos sí
> los ponemos, lo suscribimos al motor, todo, pero esos cálculos no los hacemos.
> Es justo lo más "fácil" en teoría, el TAMAR.»*

TMG27 salía con **dos pasos en FALLA** diciendo *«el ajuste tamar no tiene rama
de cálculo, la TEA va a quedar vacía»*. **El sistema sabía la respuesta y no había
forma de preguntársela**: `curvas_catalogo.fuente_valuacion('tamar')` devolvía
`None` porque TAMAR está definido en CÓDIGO (tiene pill propia) y el catálogo solo
guarda las curvas creadas sin deploy.

Se agregó `TASA_EXTERNA_EN_CODIGO = {"tamar": "jobs/tamar_1816"}` y ahora
`fuente_valuacion` mira las dos fuentes: la pregunta *«¿de dónde sale la tasa de
este bono?»* se contesta en UN lugar, sin importar dónde esté escrita la respuesta.
Con eso:

- El paso de la rama pasa a **OK**: *«a este bono NO le calculamos la tasa
  nosotros: la trae `jobs/tamar_1816`. El cuadro se guarda igual, con montos
  absolutos tal cual los manda 1816 — la conversión más simple que hay.»*
- El paso de la TEA pasa a **OK** y aclara que **el ticker entra solo** al
  universo del job (que selecciona por ajuste, no por una lista).
- El **control cruzado no aplica** y lo dice: si la tasa la trae 1816, la de ellos
  ES la nuestra — compararlas sería compararse consigo mismo y salir siempre bien.
- `jobs/tamar_1816` dejó de tener `ajuste='tamar'` hardcodeado y lee
  `ajustes_de_1816()`, que es lo que su propio docstring ya prometía. Una curva
  creada con `fuente=1816` entra sola al job.

**3. Los 202 bps de GD46, y la memoria de cálculo.**

> *«2% de diferencia de tasa es un montón, no es "se parecen". Faltan datos acá:
> qué bono se está usando, qué se toma y qué no.»*

Dos correcciones:

- **La banda bajó de 300 a 150 bps.** En renta fija 200 bps no es una convención,
  es otro bono. Clasificar eso como *«se parecen»* era ruido que enseña a ignorar
  la alarma.
- **Hipótesis fuerte sobre la causa, no verificada todavía**: el cliente de 1816
  pedía `moneda="ars"` por default, y GD46 volvió con `precioClean = 114.247` —
  un global cotiza ~60-90 por 100 VN, así que ese número **es el precio en
  PESOS**. Nuestro motor lo dividió por NUESTRO MEP para volver a dólares mientras
  1816 calculó su TEA con SU tipo de cambio: dos tasas a 202 bps **sin que ninguna
  esté mal**. Ahora se pide el precio en la **moneda del bono**, así no hay
  conversión de por medio. Se confirma mirando el próximo SIMULAR de GD46.

Y para que esto no vuelva a ser adivinanza, se agregó el bloque **CÓMO SE
CALCULÓ** (`calculo` en la respuesta, desplegable en el modal): bono y ejes,
fórmula, cuadro y escala, CER de emisión, **precio usado con su origen exacto**
(moneda, plazo, fuente y fecha del pedido a 1816), **MEP aplicado**, y **paridad y
duration nuestras contra las de ellos**. Ese último par es el que diagnostica:

- **paridad coincide, TEA no** → convención de días.
- **paridad tampoco** → es el precio o su escala.
- **duration difiere** → el cronograma que bajamos no es el mismo que el de ellos.

Una tasa sin su memoria de cálculo no se puede auditar: solo se puede creer o no
creer.

### E2.f — El bug del CER cero cupón, y qué acepta 1816 de verdad (2026-08-17)

**1. TZXM8 no daba tasa, y era un bug mío.** *«¿Por qué no se podría calcular la
tasa? Justamente lo del precio tiene que salir siempre para hacer los cálculos
previos, y ya estamos valuando bonos CER.»* Correcto en las dos cosas.

Un **CER cero cupón** tiene UN solo pago, así que caía en el atajo del bullet:
`convertir_flujos` le ponía `flujo_vencimiento` y el doc salía **sin `flujos[]`**.
Pero `calcular_campos` lee `flujo_vencimiento` **solo en la rama `tasa_fija`** —
las ramas `cer` y `soberanos` arman su cronograma desde `flujos[]` y ni miran ese
campo. Resultado: `flujos_futuros = []` → solo duration.

**Y no daba error.** El bono se veía bien cargado, con precio, sin tasa y sin
explicación — exactamente el modo de fallar que el pre-flight vino a cazar, esta
vez con el pre-flight mirando para otro lado. El atajo ahora es **solo para
`tasa_fija`**, con test numérico. Un cero cupón CER **no es una LECAP**: su pago
se ajusta por CER, por eso necesita el cronograma y no un monto fijo.

**2. La escala en ámbar era un falso positivo.** TZXM8 avisaba *«Σ 112,65, viene
en NOMINALES»*. En la rama `cer` la conversión **divide todo por `suma_amort`**
para expresar porcentajes: es invariante a la escala, y ese número no afecta a
ninguno de los valores que se escriben. Ahora el aviso sale solo en `tasa_fija` y
`soberanos`, donde los montos se guardan absolutos.

**3. `Error1816` a secas.** El mensaje del error se estaba tragando —
`type(e).__name__` da el nombre de la clase, sin el HTTP ni el motivo. Un error
que no dice qué pasó no se puede arreglar. Ahora viaja completo.

**4. `moneda="usd"` rompió GD46, y NO se va a adivinar el arreglo.** El pedido en
la moneda del bono fue una hipótesis razonable y la API la rechazó. Dos cosas:

- **Degradación**: si la moneda del bono no se acepta, se reintenta en `ars` —
  que es el default y lo que venía andando. Un precio en la moneda equivocada se
  explica mirando el detalle del cálculo; **ningún** precio deja al simulador sin
  poder calcular nada, que es peor.
- **`scripts/diag_1816_indicadores.py`** (nuevo, read-only) para cerrar el tema
  con datos en vez de intuición. Prueba **de a uno** —la API rechaza la llamada
  entera si un campo no existe— qué valores de `moneda` acepta **y qué precio
  devuelve cada uno** (que no explote no alcanza: el bug de GD46 fue que `ars`
  aceptó feliz y contestó en la moneda equivocada), y releva **~30 campos
  candidatos** contra los 6 que ya usamos.

Ese segundo relevamiento sale de una observación del user que vale la pena
subrayar: *«no estaría bueno que venga completo lo que encuentra? total nada va a
terminar persistiendo»*. **Tiene razón y cambia el criterio**: cuando un dato NO
se persiste, traer de más no tiene el costo habitual (no ensucia el modelo, no
crea una segunda verdad, no hay que migrarlo). El techo es el crédito de la API,
que acá es despreciable. Si 1816 publica `precioDirty`, `valorTecnico` o
`interesesCorridos`, el cotejo pasa de *«difieren 202 bps y no sé por qué»* a
*«difieren porque su precio es clean y el nuestro sucio, y acá está la prueba»*.

### E2.g — Los 202 bps RESUELTOS, con el spec en la mano (2026-08-17)

El user pasó el **OpenAPI de 1816** (`/v1/doc/openapi.json`) y con eso el caso
dejó de ser hipótesis. **Tres hechos verificados**, ninguno inferido:

**1. `moneda` es `ars | ccl | mep`. No existe `usd`.** Y el spec dice, textual:

> *«Para instrumentos pagaderos en moneda distinta a ARS, para calcular
> indicadores las cotizaciones **se dividen por CCL**. Default: ars.»*

**Ahí estaban los 202 bps.** Nuestro motor divide por **MEP**
(`engines/curvas.py::precio_soberano_a_usd`); ellos, con el default, dividen por
**CCL**. Dos tipos de cambio distintos sobre el mismo bono dan dos tasas
distintas **sin que ninguna esté mal** — y no había forma de verlo, porque el
parámetro que lo decide ni se estaba mandando. Ahora un bono en dólares se pide
con `moneda="mep"`: misma conversión, tasas comparables.

**2. El precio que estábamos usando era el equivocado.** `precioDirty` existe y
es el de mercado. Verificado por **consistencia interna**, no por creencia:

| | precio | ÷ paridad (0,7278) | TC implícito |
|---|---|---|---|
| `precioDirty` | 104.500 | 143.582 | **~1.436** ✔ plausible |
| `precioClean` | 114.247 | 156.975 | ~1.570 ✘ |

El que cierra con la **paridad que 1816 mismo publica** es el dirty. Que además
es lo correcto por otro camino: los bonos argentinos **cotizan sucios**, así que
el `last_price` de Primary —el que el motor espera— es dirty.

**3. Existe `/v1/mercado/indicadores/{ticker}` — INPUT MANUAL.** Le pasás UN
precio y te devuelve los indicadores calculados **a ese precio**.

**Eso es el control cruzado que faltaba, y no lo estábamos usando.** Comparar
nuestra tasa contra la de ellos tenía un agujero de fondo: cada uno la calcula
sobre SU precio, así que una diferencia podía ser la fórmula o el insumo y **no
había manera de distinguirlo**. Ahora se le pasa NUESTRO número y lo que vuelve
es su cuenta sobre la misma entrada: **lo que quede es exclusivamente convención
o cronograma**. Cuesta 3 créditos (ese endpoint cobra por campo, no por
ticker × campo).

**Y de yapa, 8 campos más** — que valen porque el simulador no persiste nada:

| Campo | Qué pregunta contesta |
|---|---|
| `convencionTna` | **si el precio es el mismo y la tasa no, la respuesta está acá** |
| `ultimaOperacion` + `volumenMontoDiario` | ¿el precio es de un trade real, o es teórico? |
| `fuente` (`byma`/`mae`/`homo-1816`) | ¿contra qué mercado estamos comparando? |
| `precioDirty`, `tem`, `durationMod`, `currentYield` | más ángulos del mismo bono |

**La lección de método** (y es la del user, no mía): *«total nada va a terminar
persistiendo»*. Cuando un dato **no se guarda**, traer de más no tiene el costo
habitual — no ensucia el modelo, no crea una segunda verdad, no hay que
migrarlo. El default se invierte: en una tabla se pide lo mínimo; en un **buffer
de diagnóstico** conviene pedir todo. Y **antes de probar de a uno, se lee el
contrato**: el OpenAPI contestó en dos minutos lo que 30 llamadas contestaban a
medias.

### E2.h — El juez del cotejo pasa a ser la PARIDAD (2026-08-17)

**La corrida del diag mostró que mi fix mejoraba pero no cerraba**, y eso obligó
a repensar qué se está comparando. Los números de GD46, con nuestra TEA en 7,69%:

| Se le pide a 1816… | su TEA | distancia |
|---|---|---|
| `ars` (el default → ellos usan CCL) | 9,71% | 202 bps |
| `ccl` | 9,79% | 210 bps |
| **`mep`** (lo que ahora se pide) | **9,08%** | **139 bps** |

Pedir `mep` bajó de 202 a 139. **Mejoró, no cerró** — y lo que queda tiene
nombre: `convencionTna = 180-360`, que es como anualiza 1816, mientras nuestro
motor usa `xirr` con fechas reales. Son **dos formas legítimas de anualizar el
MISMO flujo**. Perseguir esos bps sería perseguir un empate imposible.

**Entonces la TEA era la métrica equivocada para esta pregunta.**

> **paridad = precio / valor técnico**

Depende **solo** del precio y del cronograma de flujos — que es EXACTAMENTE lo
que el cotejo audita: *«¿el cuadro que estoy por escribir es el mismo que el de
ellos?»*. La TEA agrega dos capas que no dicen nada sobre eso: la convención de
días y, en dólares, el tipo de cambio.

Así que **la paridad es el juez y la TEA pasa a ser línea de apoyo**, mostrada
con el motivo de su diferencia (*«ellos anualizan 180-360 y nosotros con días
reales — una diferencia acá NO significa que el cuadro esté mal»*). Con eso GD46
puede salir en **verde** siendo honesto: el cuadro está bien, el método difiere.

⚠️ **Una trampa de escala** que habría hecho sonar la alarma siempre: nuestro
motor devuelve la paridad en **porcentaje** (72,78) y 1816 como **fracción**
(0,7278). Comparadas crudas dan 99% de diferencia. Está normalizado y congelado
por test — es la clase de bug que no tira error y solo produce alarmas que uno
aprende a ignorar, que es peor que no tener alarma.

**Otras dos cosas que confirmó la corrida:**

- **`moneda=mep` para un bono en PESOS es veneno**: TZXM8 devolvió
  `precioClean = 0,065` (un bono ARS dividido por el MEP). La regla tiene que ser
  por la **moneda del bono**, que es como está — pero ahora está medido.
- **El input manual funciona**: pasándole `precioDirty = 104500` devolvió
  exactamente su propia TEA (0,09709583…). Eso **prueba que `precioDirty` es el
  insumo con el que ellos calculan** — la confirmación que faltaba.

### E2.i — El alta entra COMPLETA: ficha + tasa externa (2026-08-17)

Dos preguntas del user sobre TMG27, y las dos destaparon huecos reales.

**1. «Si inserta un TAMAR/BADLAR hay que meterlo YA con el margen y la TEA que
haya. De un TAMAR el MARGEN es fundamental.»** No lo tenía contemplado.

`aplicar()` escribía la fila en `mercado.curvas` y listo. La tasa y el margen
aparecían recién cuando corriera `jobs/tamar_1816` — **hasta 30 minutos en rueda,
y hasta el día siguiente fuera de ella**. Un alta que deja vacío el dato
principal del instrumento está a medio hacer, y en un TAMAR ese dato es el
margen: no es un adorno, es lo que la mesa mira.

Ahora, cuando el ajuste tiene `fuente_valuacion='1816'`, el alta **le pide a 1816
su última tasa** (con `indicadores_vigentes`, o sea retrocediendo día hábil por
día hábil: al alta le sirve el ÚLTIMO dato que exista, no específicamente el de
hoy) y la deja escrita en `mercado.tamar_1816`. Queda en el libro de acciones
como `sembrar_tasa_1816`, y el pre-flight lo avisa ANTES de aplicar.

El upsert se movió a **`core/tamar_1816_sql.py`** para que el job y el agente
usen el mismo — reescribirlo habría dado dos INSERT que se separan solos. Y si
1816 no publicó tasa (el caso de TMG27 hoy), **no se escribe una fila en NULL**:
la vista mostraría el bono "con dato" y el dato sería nada. Se reporta y el job
la completa cuando aparezca.

**2. «¿Esto agrega el bono en curvas con todos sus campos completos?»** **No**, y
la medición es clara: `upsert_bono` acepta **19 campos** y el agente mandaba
**14**. Quedaban vacíos cinco:

| Campo | De dónde sale ahora |
|---|---|
| **`emisor`** | 1816, que **es la fuente de verdad** (`jobs/ficha_1816` midió 74 strings para 67 emisores reales antes de estandarizar) |
| `fecha_emision` | 1816 — **ya la estábamos leyendo** para inferir el CER, y no la escribíamos |
| `tipo` | derivado de los ejes (Global / Bonar / ON / Lecap / Bono) |
| `tasa_referencia` | el ajuste, para TAMAR/BADLAR/TPM |
| `cupon_anual` | **solo si es cero cupón**, que es el único caso inequívoco |

Los cuatro primeros salían de datos que ya teníamos —el emisor estaba a un
`SELECT` de distancia sobre `research.mkt_1816_instrumentos`, 0 créditos— y el
bono nacía sin ellos. `cupon_anual` con cupones de por medio exigiría **asumir la
frecuencia**, y asumir es justo lo que no se hace: se deja vacío y se dice.

**Y la respuesta a esa pregunta ahora vive en la pantalla, no en un chat.** Se
agregó el paso **«El bono entra COMPLETO, no pelado»**, que lista qué campos se
van a escribir con sus valores y cuáles quedan vacíos con el motivo. *«¿Entra
completo?»* es una pregunta legítima que antes no se podía contestar mirando.

### E2.j — «1816 te manda el último precio, no entiendo tanto bardo» (2026-08-17)

El user, sobre la cadena de TMG27:

> *«Esa fecha que estás tomando es cualquiera. Para el precio es mañana 17…
> hoy es domingo, el último precio es del 14. Siempre va a dar error si lo
> consultás un domingo. Si yo consulto a 1816 me devuelve un precio y listo.»*

Tenía razón, y el bug **lo introduje yo en E2.g**.

**La causa.** `indicadores_vigentes` retrocede día hábil por día hábil hasta
encontrar una rueda con datos. El predicado de *«esta rueda trajo datos»* era
`any(campo is not None)` sobre los campos pedidos. Mientras el simulador pidió
**4 campos —todos de valor—** funcionó. En E2.g la lista se amplió a **13** para
enriquecer el diagnóstico, y ahí entraron `fuente`, `convencionTna` y
`fechaLiquidacion`: **metadata que 1816 devuelve SIEMPRE**, haya operado el papel
o no. Con eso el predicado daba **True en la primera vuelta** y el retroceso
**nunca corría**. Reproducido antes de tocar nada:

```
El predicado de "trajo datos" es: any(campo is not None)  →  True
  ¡y NO hay un solo precio!  Los culpables:
     convencionTna = '180-360'   fuente = 'byma'   fechaLiquidacion = '2026-08-18'
```

Es el tipo de regresión que no rompe nada visible: la función seguía devolviendo
una respuesta válida, solo que de la rueda equivocada. **La lección de diseño**:
un predicado que depende de *«qué campos pediste»* cambia de significado cada vez
que alguien agrega un campo — y nadie que agrega un campo va a pensar que está
tocando la lógica del retroceso.

**Tres cambios, en capas distintas a propósito:**

1. **`CAMPOS_METADATA`** (`core/mercado_1816.py`) — la ficha del pedido no cuenta
   como dato. Ahora agregar un campo al diagnóstico es inofensivo.
2. **`campos_dato=`** — porque *«¿trajo datos?»* **depende del que pregunta**: a
   `jobs/tamar_1816` le alcanza con la tasa, pero el simulador necesita el
   **PRECIO** (es el insumo del motor) y una rueda con TEA modelada y sin
   operaciones lo deja igual de plantado que una vacía. El agente pasa
   `("precioDirty", "precioClean")`; el default no cambia para nadie más.
3. **El mensaje.** Decía *«no publicó precio al 2026-08-17»* nombrando un día sin
   mercado, lo que se lee como «1816 está roto». Ahora dice **qué ruedas se
   probaron** (`{n} ruedas probadas (2026-08-18 → 2026-08-14)`) y, cuando la
   respuesta existe pero sin precio, muestra **`ultimaOperacion`** — que es la
   respuesta real casi siempre: *el papel no opera*, no *el pedido está mal*.

Congelado con tres tests: metadata-no-es-dato, pedir-solo-metadata-no-agota-el-
retroceso, y el formato de los dos mensajes de fracaso.

### E2.k — LOS CINCO ESTADOS: qué frena el alta y qué es solo informativo (2026-08-17)

**El incidente.** GD46 simulado mostró esto, en ámbar, como un aviso más:

```
▲ El cuadro coincide con el de 1816
  paridad nuestra 0,05% vs 1816 75,56% → 99,94% de diferencia. Se contradicen.
  No se bloquea el alta (el umbral es un primer corte, no una medición).
```

…y arriba, el veredicto: *«la cadena cierra: se puede aplicar»*, con el botón
APLICAR habilitado. El user: *«esto es grave. Que me diga que está OK cuando algo
tan clave como esto está así… ahí no puede haber un warning».*

**La causa no era el umbral: era el modelo de estados.** `atencion` significaba
DOS cosas incompatibles al mismo tiempo:

| se leía igual | pero es | |
|---|---|---|
| «la paridad se contradice 99,94%» | una **prueba** de que el cronograma es otro | |
| «el alta va a sembrar la especie» | una **consecuencia** del alta, ni buena ni mala | |
| «hay que reiniciar los motores» | un paso posterior, siempre igual | |
| «no hay unidad en assets» | normal: nadie tiene el bono todavía | |

Con las dos categorías en el mismo triángulo ámbar, el contador decía *«6 a
mirar»* en cadenas donde no había nada para mirar. **Un contador que grita
siempre se deja de leer** — y así es como una contradicción real pasa
desapercibida. El user lo dijo entero: *«hay algunos que no van con warning, o
sea no son ni buenos ni malos… vos ya deberías saber que es un check verde o una
cruz roja y listo, el resto son informativos»*.

**El modelo nuevo — cada estado significa UNA cosa:**

| | estado | significa | ¿frena a mano? | ¿frena al robot? |
|---|---|---|---|---|
| ✔ | `ok` | verificado y correcto | no | no |
| ○ | `info` | ni bueno ni malo: lo que va a pasar | **no cuenta para nada** | no |
| ▲ | `revisar` | se midió y NO cierra, sin ser concluyente | no | **sí** |
| ✖ | `bloquea` | probado mal | **sí** | sí |
| ? | `no_se` | no se pudo verificar | no | **sí** |

Los dos del medio existen porque colapsarlos en un extremo sería mentir: 2,4% de
diferencia de paridad no está probado mal, y *«no pude leer la base»* no es *«está
bien»* (REGLA #2). Pero **ninguno de los dos es informativo: los dos paran al
robot**, que es la pregunta que el user vino a resolver — *«el día de mañana que
se haga solo, esto es fundamental»*.

De ahí salen **dos booleanos, no uno**, y viajan resueltos desde el backend:
`puede_aplicar` (para el humano) y `puede_auto` (para la lane automática de E6).

**Tres bugs que el rediseño destapó, todos del mismo patrón — dos lugares
contestando la misma pregunta:**

1. **El cotejo de paridad** pasó de `atencion` a **`bloquea`**. El argumento
   viejo («el umbral es un primer corte, no una medición») estaba mal: el umbral
   decide cuándo alarmarse, pero cruzado por 20 veces lo que hay es una
   demostración. Y lo que se escribiría es un bono con una TEA plausible y
   equivocada: **no falla, miente**.
2. **«Hay precio para simular la tasa» salía en VERDE ✔** con el texto *«pero el
   motor NO devolvió TEA: revisar la escala»* en el mismo renglón. Un tilde verde
   sobre la descripción de una falla — y no es sospecha: se le dieron al motor el
   cuadro real y el precio real y no calculó. Ahora **bloquea**, con la excepción
   que lo hace correcto: en un TAMAR/BADLAR que el motor no dé TEA es lo
   **esperado** (la trae 1816), y ahí sigue verde.
3. **TMG27 mostraba la cadena entera en verde y SIN botón APLICAR.** `aplicable`
   hacía `rama in RAMAS_AUTOMATICAS` mientras el paso de la cadena usaba
   `_alta_automatica`, que además acepta el camino de la tasa externa. Dos
   funciones, una pregunta. Ahora `aplicable` llama a `_alta_automatica`, y el
   botón lo gobierna `veredicto.puede_aplicar` — una sola fuente.

**Y el botón que desaparece ahora explica por qué**: donde estaba APLICAR aparece
`✘ BLOQUEADO`. Un control que se esconde no distingue «te lo frené» de «falta
cargar algo».

`aplicar()` valida contra el MISMO `puede_aplicar` que mira el front, así que
esconder el botón y rechazar la escritura no pueden desincronizarse — y pegarle
al endpoint a mano tampoco saltea el bloqueo.

### E2.l — AVISOS: el agente hace el 95% y anota el 5% (2026-08-17)

Tres cosas de la corrida de TZXA7, y las tres son la misma idea de fondo.

**1. El CER de emisión BLOQUEABA, y era la decisión equivocada.** El user:

> *«Está bien que se cargue sin CER de emisión. Solamente tiene que haber una
> sección acá en el agente que se llame AVISOS, y todo lo que aparezca ahí es
> para hacer manual. A los CER les perdonamos: igual me saca el laburo de
> cargarlo en la base y hacer todo el trabajo, me lo deja sencillo, solo poner el
> CER de emisión y nada más.»*

Tiene razón y cambia la postura del agente. El alta baja los flujos, resuelve los
ejes, completa la ficha, siembra las especies y valida la cadena entera. Negarse
a todo eso porque falta **un número que ninguna fuente publica** es tirar el
trabajo hecho. Ahora el CER faltante es `revisar` + **aviso**: el bono entra y el
pendiente queda anotado. Sigue frenando la lane **automática** — un robot dejaría
el bono sin tasa y sin nadie enterado.

**La tab AVISOS se DERIVA, no se persiste.** Mismo criterio que SALUD: un aviso
guardado hay que acordarse de cerrarlo, y una lista de pendientes que nadie
limpia se deja de mirar a la semana. Acá **el aviso ES la condición** — cargás el
`cer_emision` y la fila desaparece sola. No hay botón de «resuelto» porque no
hace falta, y no puede quedar desactualizada. Alcance: los bonos que dio de alta
**el agente**; auditar los 221 cargados a mano en dos años es otra pregunta.

**2. Una causa, tres pasos en rojo.** El user, sobre el paso del precio:

> *«No lo entiendo, no es claro. Si hay flujo y hay precio, ¿por qué no podrías
> simular? Y 1816 tasa tiene.»*

**Verificado en `engines/curvas.py:438`**: la rama CER, sin `cer_emision`,
devuelve solo `duration` y sale. O sea que el paso 4 (falta el CER) **causaba**
el paso 8 (el motor no dio TEA) y el paso 9 (no hay paridad para cotejar). La
pantalla mostraba tres problemas donde había uno — y el paso 8 encima mandaba a
*«revisar la escala del cuadro y la pata»*, que no tenía nada que ver.

**Un diagnóstico que apunta al lugar equivocado es peor que no tenerlo**: hace
perder el tiempo buscando donde no está. Ahora, cuando la causa se conoce, el
paso consecuente la nombra (*«sin TEA: falta el CER de emisión»*) y **deja de
contar como hallazgo propio** — el que frena es la causa, no el síntoma.

**3. Los mensajes eran ensayos.** *«En general pasa que los mensajes son mucho
texto y poco claros, muchas palabras.»* Reescritos todos a una línea:

| antes | ahora |
|---|---|
| «sin snapshot todavía, así que se usó el **precio de referencia de 1816**: 120.75 al 2026-08-14 — pero el motor **NO devolvió TEA** con este cuadro y este precio. No es una sospecha: se le dieron los dos insumos reales y no calculó, así que el bono nacería con la celda de tasa vacía. Ese precio NO se guarda: es solo para poder calcular antes de aplicar.» | «precio de 1816 120.75 (2026-08-14, no se guarda) → sin TEA: falta el CER de emisión (paso de arriba)» |

La regla que queda: **el detalle de un paso es una línea**. Si necesita un
párrafo, el que sobra es el párrafo, no el lugar donde ponerlo.

### E2.m — El aviso lo cierra una PERSONA, y la foto se coteja (2026-08-17)

**1. Me equivoqué en el diseño de los avisos.** En E2.l los DERIVÉ del estado del
master —cargás el `cer_emision` y la fila desaparece sola— y argumenté que era
mejor. El user:

> *«Justamente la idea es aplicarlo y que quede el aviso de que le falta el CER,
> y solo desaparezca cuando yo marque el aviso como ejecutado.»*

Tiene razón por dos motivos que no vi. **El aviso es SU lista de tareas**, no un
reporte de estado: una lista que se borra sola no deja ver qué había pendiente ni
qué se hizo. Y derivarlo confunde dos cosas distintas — *«el dato está»* y *«yo
ya me ocupé de esto»*.

Ahora se persiste en `mercado.av_agent_avisos`, nace al APLICAR y lo cierra el
user (`POST /api/ia/av-agent/aviso`, reversible como `designorar`).

**Pero el cierre manual solo es seguro si algo lo contrasta.** Un aviso marcado
como hecho sobre un dato que sigue faltando mentiría en silencio — el riesgo que
la derivación no tenía. Por eso cada fila viaja con **`ya_cargado`**: el cruce
contra el master en vivo, en la misma query. La pantalla lo dice en los dos
sentidos: *«⚠ el dato sigue faltando»* sobre un aviso cerrado, y *«✔ ya está
cargado — podés marcarlo»* sobre uno abierto. **La decisión es del user; la
verificación es del sistema.**

**2. «Algo tremendo»: TZXM8 ya estaba en curvas y seguía en la lista de
faltantes.** Y la intuición del user sobre el motivo era correcta —*«entiendo que
es porque no se ejecutó de nuevo»*—: la lista es una **foto** de la última
corrida, y relevar cuesta ~29 créditos y 1-2 minutos de throttle, así que no se
puede rehacer cada vez que se abre la pantalla.

**Pero mostrar como faltante un bono que el agente MISMO acaba de crear destruye
la confianza en toda la lista**: si una fila está mal, ninguna vale. Y no hay
forma de que el user distinga cuáles caducaron.

La salida no es rehacer la foto: es **contrastarla contra la realidad antes de
mostrarla**. Un `SELECT ticker FROM mercado.curvas` —una query, el peaje fijo de
~8,5 ms— alcanza para tachar los `falta_en_base` y `hueco_de_curva` que ya
existen. Solo esos dos tipos caducan: un `sin_flujo` habla de un bono que YA está
en el master, así que estar ahí no lo resuelve.

**Es el mismo principio que el `ya_cargado` de los avisos, y vale como regla
general del agente: la foto se muestra, pero nunca sin cotejarla.** Un dato
persistido que se puede contradecir con una query barata debe contradecirse
siempre — mostrarlo crudo es más rápido de escribir y más caro de confiar.

### E2.n — El mismo bug por TERCERA vez: se elimina el segundo gate (2026-08-17)

TZXA7, con la cadena diciendo **«se puede aplicar A MANO»** y sin botón APLICAR.
El user: *«¡pero no figura el aplicar!»*.

**Es el tercer episodio del mismo bug, y las tres veces lo miré mal.** El
diagnóstico correcto no era el valor de `aplicable`, era su EXISTENCIA:

| # | caso | qué decía `aplicable` |
|---|---|---|
| 1 | TMG27 (E2.k) | `rama in RAMAS_AUTOMATICAS` — no aceptaba el camino de la tasa externa |
| 2 | TZXA7 (E2.m) | quedó `and not (cer and not cer_emision)`: cuando el CER dejó de bloquear la cadena en E2.l, **este renglón lo siguió bloqueando** |
| 3 | el front | hacía `aplicable && puedeAplicar` — y **un AND convierte al más restrictivo en el gate real**, que es justo el que nadie está mirando |

En E2.k arreglé la mitad de la expresión y dejé la otra. En E2.l hice que el CER
no bloqueara *la cadena* sin notar que había un segundo lugar donde sí bloqueaba.
**Arreglar el caso deja viva la estructura que lo produce.**

**El arreglo estructural: `aplicable` deja de gatear.** Su único contenido
legítimo —¿la rama convierte sin ambigüedad?— **ya es un paso de la cadena**
(`rama`, que pone BLOQUEA cuando no), así que `veredicto.puede_aplicar` lo cubre
entero. Ahora `aplicable` solo pinta el motivo en la pantalla, se borró el
`if not sim["aplicable"]: return` de `aplicar()`, y el front lee **una sola
condición**. Congelado con un test que falla si alguien vuelve a meter la
condición del CER en un gate.

**La lección, que vale para todo el sistema:** dos gates para una decisión no se
contradicen *si alguien se equivoca* — se contradicen **siempre, tarde o
temprano**, porque solo uno se actualiza. La pregunta al revisar no es «¿cuál de
los dos está bien?» sino «¿por qué hay dos?».

De yapa: el encabezado seguía diciendo *«revisar la escala del flujo o la pata»*
mientras la cadena, dos renglones abajo, decía que falta el CER de emisión —
misma causalidad de E2.l, aplicada también a esa línea.

### E2.o — RAMA y CURVA son dos vocabularios (2026-08-17)

El libro de acciones, con el alta de TMG27 fallada dos veces:

```
✘ Dio de alta el bono   TMG27   mercado.curvas
  curva inválida: 'otros' (válidas: tasa_fija, cer, soberanos, dolar_linked, tamar, dual)
```

**El agente clasificó BIEN.** Los ejes salieron correctos —`soberano · ARS ·
tamar`— y el paso de la rama dijo lo que corresponde: *«la tasa la trae
jobs/tamar_1816»*. Lo que falló fue una **traducción en el último paso**:

| | valores |
|---|---|
| **rama** — qué FÓRMULA usa el motor | tasa_fija · cer · soberanos · dolar_linked · **otros** |
| **curva** — qué TIPO de instrumento es | tasa_fija · cer · soberanos · dolar_linked · **tamar** · **dual** |

`aplicar()` mandaba `curva = rama`, con un comentario que afirmaba que eran el
mismo valor. **Coinciden en cuatro de cinco, que es exactamente por qué sobrevivió
tanto**: solo se rompe con TAMAR y con los duales. Y `otros` no significa «no sé
qué es»: significa **«no le calculamos la tasa nosotros»** — una clasificación
correcta, copiada a un campo que habla otro idioma.

⚠️ **`curva='soberanos'` NO es «soberano en pesos».** Es la curva hard-dólar
(globales/bonares) y su matemática pasa por el MEP. El eje `emisor_tipo=soberano`
es otra cosa: un TAMAR es soberano Y su curva es `tamar`. Mapearlo a `soberanos`
—que es lo que sugiere la intuición— lo habría valuado con la cuenta equivocada
sin un solo error en pantalla, que es el modo de falla favorito de este dominio.

**Sin equivalente no se inventa uno.** `badlar`, `tpm` y `caucion` son ajustes
válidos sin curva en `CURVAS_BONO`: `curva_destino` devuelve `""` y el pre-flight
BLOQUEA con el motivo, en vez de escribirlos bajo una curva parecida y que la
vista los agrupe mal para siempre.

**Y el paso que faltaba: «La escritura va a ser aceptada».** El user: *«¿por qué
lo permitió aplicar?»*. Porque el pre-flight validaba 13 cosas sobre los DATOS y
**ninguna sobre si la escritura iba a entrar**. Ahora valida el campo contra la
MISMA constante que usa el writer, así que no puede desincronizarse — y es el
chequeo más barato de los 14.

### E2.p — Dos fuentes para «¿qué curvas existen?» (2026-08-17)

El user, viendo BADLAR reportada como «ajuste sin curva» con la pill BADLAR ya
visible en renta fija: *«ya existe, debería dejar de decir que no está»*. Y de
paso, el alta de TB27 bloqueada por esa misma curva.

**Son dos síntomas de la misma causa, y los dos ya me los habían señalado antes
en otra forma.**

**1. El hallazgo caducado que mi filtro no tapaba.** En E2.m agregué el cotejo de
la foto contra el master, pero apliqué **UN predicado a dos tipos de hallazgo
cuyo campo `ticker` significa cosas distintas**: en un `hueco_de_curva` el
`ticker` es el **AJUSTE** (`BADLAR`), no un bono, así que compararlo contra
`mercado.curvas.ticker` no lo sacaba nunca.

Ahora cada tipo caduca por su propia razón, y la de éste es `ajuste_sin_curva`
—**la MISMA función que lo detecta**, que ya lee el catálogo—, así que
preguntarle de nuevo no puede dar un criterio distinto.

**2. `CURVAS_BONO` era una tupla a mano y por eso mentía.** El agente puede CREAR
curvas (E1.h) y creó `badlar`; la constante no se enteró, y el alta de un BADLAR
se rechazaba **por una curva que el propio sistema ya tiene**. Ahora la constante
es el **piso** y el catálogo la **amplía** (`curvas_validas()`): crear la curva
deja el alta habilitada en el mismo acto, sin tocar código. Y el paso del
pre-flight dejó de sugerir *«agregar badlar a `bonos_admin.CURVAS_BONO`»* —que era
pedirle al user que edite código— para decir *«crear la curva desde el agente, y
el alta queda habilitada sola»*.

**El patrón, por cuarta vez en la semana:** dos fuentes para la misma pregunta,
solo una se actualiza. `aplicable` vs el veredicto (E2.k, E2.n), rama vs curva
(E2.o), y ahora el catálogo vs la constante. **La pregunta de review no es «¿cuál
de los dos está bien?» sino «¿por qué hay dos?»** — y la respuesta correcta casi
siempre es derivar uno del otro, no sincronizarlos a mano.

### E2.q — Lo que no cotiza no es un hallazgo, y el dólar-linked no era ambiguo (2026-08-17)

**1. Primary es el filtro más duro, y el user lo dijo mejor que el código:**

> *«Si no está en Primary ni me interesa, ya que si no le puedo meter el last
> price no tiene valor. ¿Cómo hacemos para que no aparezca constantemente?»*

Un bono que no cotiza **no se puede valuar nunca**, así que no es un hallazgo: es
ruido permanente que empuja hacia abajo a los que sí importan. Ahora el detector
lo **descarta**, con dos salvaguardas:

- Se prueban las **dos patas** (`24hs` y `CI`) antes de descartar — el símbolo se
  arma por convención y un bono que cotiza solo en contado inmediato existe.
  Descartar de más acá es **invisible**, así que el criterio es generoso.
- **Si la casa lo TIENE en cartera se reporta igual**, aunque no cotice: ahí el
  problema es más grave, no menor —una posición que no valúa— y esconderlo sería
  lo contrario de lo que hay que hacer.
- Y **no se tiran en silencio**: van al log con su cuenta. *«No reporté 37 porque
  no cotizan»* es información; *«no aparecen»* es un agujero.

Sin universo de Primary NO se filtra (misma degradación que
`core/instrumentos_validos`, cuya función se reusa): filtrar de más esconde bonos
reales, no filtrar deja el ruido de siempre — ante la duda, lo segundo.

**2. El dólar-linked no tenía por qué bloquear.** `engines/curvas.py:597` dice,
textual, que sus flujos usan **«el shape porcentual sobre VN igual que
soberanos»** y llama a la MISMA `monto_flujo_soberano`. Excluirlo de
`RAMAS_AUTOMATICAS` era una hipótesis mía que el propio motor desmiente — y el
pre-flight se contradecía solo: el paso 3 decía *«no se puede convertir sin
ambigüedad»* y el 10, tres renglones abajo, *«la rama dolar_linked tiene fórmula
en engines/curvas.py»*.

⚠️ **Pero la ESCALA sí era un problema real, y no el que yo pensaba.** Un
soberano de 1816 viene en base 100 (GD46 midió Σ=100,000012); los dólar-linked
vienen en **nominales de la emisión** — D10Y7 y D30O6 miden **Σ=148.869,84**.
Pasar eso crudo como «pct» daría un valor técnico ~1.489 veces más grande y una
TEA absurda **sin ningún error**. Por eso la conversión **normaliza por la Σ**,
igual que la rama CER: con Σ≈100 la operación es la identidad, así que es
correcta en los dos casos.

**3. El «paso 15» fantasma.** Al final de la cadena aparecía un renglón sin
número, sin icono y sin título, repitiendo la nota de Primary. Era un `<li>`
suelto DENTRO del `<ol>`, escrito cuando el paso de Primary todavía no existía.
Hoy el paso 6 lo dice con su icono, su tabla y su acción — la copia se borró.

### E2.r — El filtro tiene que correr también AL LEER (2026-08-17)

Los BPO seguían apareciendo con el filtro de Primary ya escrito. **Porque lo puse
solo en el DETECTOR**, que corre al relevar — y relevar cuesta ~29 créditos, así
que no se hace por pantalla. El filtro no iba a surtir efecto hasta la próxima
corrida.

**Es la tercera vez que cometo el mismo error en la misma función**, y las tres
veces yo mismo había escrito la regla: *«la foto se muestra, pero nunca sin
cotejarla»* (E2.m). La escribí para TZXM8, no la apliqué a BADLAR (E2.p), y
tampoco al filtro que acababa de agregar.

**Por qué se repite**: un filtro puesto en el detector *parece* completo —el
código dice lo correcto, los tests pasan— y el síntoma solo aparece en producción,
horas después, en una lista que nadie relaciona con el cambio. La corrección no es
acordarse: es que **el predicado viva en un solo lugar y lo llamen los dos**.
Ahora `descartar_por_primary()` es esa función, y la usan el detector y la lectura
— con la excepción de cartera incluida adentro, así que no puede aplicarse en un
lado y olvidarse en el otro.

**Regla general que queda para el agente**: *todo criterio que decida si algo se
muestra tiene que poder evaluarse en la LECTURA.* Si solo se puede evaluar al
relevar, el usuario ve el criterio viejo hasta la próxima corrida — y como no
tiene forma de saber cuál está viendo, deja de creerle a la lista entera.

### E2.s — GD46: el flujo estaba perfecto, la UNIDAD del precio no (2026-08-17)

El GD46 mostraba una **paridad de 0,0455 contra 0,7556 de 1816** y una duration de
19,9 años contra 6,61. Con esos dos números en pantalla la conclusión intuitiva es
que el cuadro de flujos está mal — y era exactamente al revés.

**La aritmética que lo cierra** (hecha con los números de la propia pantalla, no
estimada):

```
precio usado        69          (1816, ya en USD)
MEP aplicado        1.517,6262
69 / 1517,6262   =  0,045466    ← clavado el "paridad nuestra 0,0455" de la pantalla
valor técnico implícito de 1816 = 69 / 0,7556 × 100 = 91,315
paridad correcta =  69 / 91,315 × 100 = 75,57 %   vs   1816 dice 75,56 %
```

Un centésimo de diferencia. **El cuadro de flujos era correcto desde el principio**:
lo que estaba mal era la unidad del precio que le entraba.

**La causa.** `engines/curvas.py::precio_soberano_a_usd` decide por el **sufijo del
símbolo**: `…D`/`…C` → el precio ya viene en dólares y lo devuelve tal cual; sin
sufijo → asume pesos y **divide por MEP**. El simulador arma
`MERV - XMEV - GD46 - 24hs` (sin sufijo, porque es el símbolo que corresponde) pero
le pedía el precio a 1816 con `moneda="mep"`, o sea **ya en dólares**. El motor
volvía a dividir: dos conversiones para una sola moneda, y una paridad 1.500 veces
más chica.

**Es mi bug, introducido en E2.g.** Ahí resolví los 202 bps eligiendo pedirle a 1816
el precio en la moneda del EJE del bono. Correcto para el cotejo, incorrecto como
insumo del motor: el motor no mira `moneda_eje`, mira el sufijo del símbolo.

**Por qué no dio error.** Sin TEA calculable el motor devuelve la duration naive
(19,9 ≈ años al vencimiento) y sigue. Una paridad absurda no rompe nada: se
muestra. Es el mismo patrón que RAMA/CURVA de E2.o — **dos vocabularios que se
parecen y no son el mismo**: la moneda del eje contable y la moneda del símbolo de
mercado.

**El fix es estructural, no un `if` más.** `moneda_pedido_1816(simbolo, moneda_eje)`
**deriva** la moneda del pedido del **mismo predicado que usa el motor** (el sufijo
del ticker), en vez de elegirla por separado. Si mañana cambia la regla del motor,
lo que hay que tocar es un lugar, no dos que se contradicen en silencio.

**Regla que queda**: *cuando le pasás un dato a un motor, la unidad la decide el
motor, no vos.* Elegir la unidad "por lógica de negocio" —el eje del bono— y que el
consumidor use otro criterio —el sufijo— es el mismo anti-patrón de las dos fuentes
para una pregunta, disfrazado de conversión.

### E2.t — Dos síntomas iguales, dos causas distintas, y ninguna es el cuadro (2026-08-17)

GD46 y D30O6 fallaban los dos con la misma cara —«revisar la escala del flujo o la
pata»— y son problemas completamente distintos. **En ninguno de los dos el cuadro
de flujos estaba mal.**

**D30O6 (dólar-linked) — el A3500 no se cargaba NUNCA.** La rama `dolar_linked` de
`engines/curvas.py` arranca así: sin `tc_a3500` sale por la puerta de emergencia
con la duration ingenua y sin TEA. El simulador **nunca le pasaba ese argumento**:
solo mandaba `mep`, y decidía si hacía falta preguntando `moneda_flujo == "USD"`.

Es **otra vez dos criterios para una pregunta**. El motor decide qué tipo de
cambio necesita por **RAMA** (`curva_depende_de`); el simulador lo decidía por
**moneda del flujo**. Un dólar-linked tiene `moneda_flujo` USD y necesita A3500,
no MEP — con ese criterio no cargaba ninguno de los dos. La prueba está en la
pantalla: duration 0,2027 = 74/365, los días al vencimiento clavados, que es lo
único que devuelve esa salida de emergencia.

El fix es el mismo patrón que E2.s: el simulador ya no tiene criterio propio,
llama a `curva_depende_de` — la función que usa el motor para invalidar su cache.

Y la nota del encabezado deja de mentir: falta de TC ahora dice **«sin TEA: falta
el tipo de cambio A3500 (feed MAE) — no es el cuadro»**, en vez de mandar a
revisar una escala que está bien (misma regla de causalidad de E2.l).

*Verificado por aritmética, no supuesto*: 1816 publica paridad 0,99207 sobre un
precio de 147.690 → su valor técnico es 148.869,84, **exactamente la Σ de nuestro
cuadro**. O sea que 1816 manda el cronograma del dólar-linked ya pesificado a un
TC de 1.488,6984 (= Σ/100), y normalizarlo por la Σ —lo que hace E2.q— es
correcto. Con el A3500 cargado, nuestra paridad tiene que dar ≈ 99,2%.

**GD46 (soberano) — la paridad usa un residual que no le pasamos.** Acá el fix de
E2.s funcionó: el precio ya entra bien (104.500 en pesos ÷ MEP = 68,86 USD). Pero
`engines/curvas.py:542` calcula la paridad como `precio_usd / residual_previo_pct`
y **nuestro conversor de la rama `soberanos` no escribe ese campo** — el motor lo
defaultea a 100, así que la paridad sale igual al precio en dólares: 68,86%. 1816
dice 72,78%.

**Acá NO se codea a ciegas (REGLA #2).** La aritmética dice que el residual de
ellos es ≈ 94,8 (68,859/94,8 × 100 = 72,64%, y ajustando por la diferencia de TC
—1.517,63 contra el 1.514,49 implícito de 1816— da 72,79% contra su 72,78%). Pero
**de dónde sale ese 94,8 no se puede afirmar sin ver la respuesta cruda**: la Σ de
las amortizaciones que manda 1816 es 100,000012, o sea que su cuadro parece venir
ya renormalizado al residual vivo, y entonces el residual **no está en el cuadro**.
Escribir `residual_previo_pct` derivándolo de esa Σ daría 100 otra vez — un cambio
que no cambia nada.

Por eso el entregable es un diag, no un fix: `scripts/diag_av_agent_flujos.py`
imprime el cashflow crudo con todas sus claves, sondea **de a un nombre por vez**
si la API acepta un campo de residual/valor técnico (1816 rechaza la llamada
entera si uno no existe — trampa ya pagada en `jobs/tamar_1816`), y compara el
valor técnico implícito en `ars` contra el de `mep`.

**De paso**: el cuadro «CÓMO SE CALCULÓ» mostraba «paridad nuestra 68,8575 · 1816
0,727808». El paso del cotejo ya normalizaba las escalas —nuestro motor devuelve
porcentaje, 1816 fracción— pero el cuadro no, y eso se lee como un error de 100×
cuando la diferencia real era del 5%. Ahora las dos van en la misma unidad.

### E2.u — GD46 cerrado: el residual estaba en el cuadro, la paridad no era comparable (2026-08-17)

`scripts/diag_av_agent_flujos.py` contra prod contestó las dos preguntas que E2.t
dejó abiertas, y las dos hipótesis que yo tenía eran falsas.

**Hipótesis mía: «1816 manda el cuadro renormalizado al residual vivo».
FALSA.** Manda el cronograma **COMPLETO desde la emisión**: GD46 devuelve 51
cupones, el primero del **2021-07-12**, con Σ amortizaciones = 100,000012. O sea
que el residual vivo **sí se deriva del cuadro** — hay que descontar lo ya
amortizado y leer el residual del primer flujo FUTURO, que es exactamente lo que
`convertir_flujos` ya calcula para la rama CER y no escribía en la de soberanos.

La aritmética, con el cuadro real en la mano: 44 amortizaciones iguales de
2,272739 a partir del cupón 8; al 2026-08-17 van 4 pagadas → **residual 90,909**.
1816 publica un valor técnico de **91,3153**: la diferencia, **0,41, es el interés
corrido**, que nuestra paridad no incluye por definición.

**Hipótesis mía: «el residual viaja en algún campo que no pedimos». FALSA.** El
sondeo probó 8 nombres en `/cashflow` y 6 en `/indicadores`, de a uno: **los 14
rechazados**. El vocabulario de la API es el que ya usábamos.

**Y apareció lo que no estaba buscando: la paridad de 1816 depende de la moneda
del pedido, y no como una simple reexpresión.** Para GD46:

| pedido | paridad | precio dirty | VT implícito |
|---|---|---|---|
| `mep` | 0,7556 | 69 | 91,3153 |
| `ars` | 0,7278 | 104.500 | 143.581,80 |

El precio va a un TC de 1.514,5 y el valor técnico a uno de **1.572,4**. No son el
mismo número en dos monedas. Nuestra paridad contra la de `ars` quedaba **4,07%
afuera**; contra la de `mep`, **0,24%**.

Eso obliga a separar **dos preguntas que yo venía tratando como una**:

```
moneda_pedido_1816  →  ¿en qué moneda quiere el motor su INSUMO?    soberano: PESOS
moneda_cotejo_1816  →  ¿en qué moneda devuelve el RESULTADO?        soberano: USD
```

Un soberano cotiza en pesos —el motor los divide por el MEP— y devuelve la paridad
en dólares. Así que el precio se pide en `ars` (E2.s) y el cotejo se hace en `mep`,
pasándole a 1816 **el mismo número que consumió el motor**: el precio ya convertido
por `precio_soberano_a_usd`, la función del motor y no una copia.

Las demás ramas siguen en `ars` a propósito, y **medido**: en `dolar_linked` la
paridad es un cociente entre dos números pesificados al mismo TC —no depende de la
moneda— y encima 1816 no publica `mep` para ellos (D30O6 devolvió todo `None`).

Resultado: **paridad nuestra 75,74% contra 75,56% de 1816 → 0,24%**.

**Lo que queda de lección.** Las tres iteraciones de GD46 —E2.g, E2.s, E2.u— fueron
el MISMO error con tres caras: **tratar «la moneda» como una sola pregunta cuando
son tres** (la del eje contable, la que espera el motor, la en la que devuelve el
resultado). Cada vez que dos de esas coincidían el bug se escondía, y cuando no
coincidían no daba error: daba un número plausible. El antídoto no es más cuidado,
es **nombrar cada pregunta y darle su función**.

Y un corolario para el cuadro de auditoría: mostraba la paridad de `ars` mientras
el paso del cotejo decidía con otra. **Un cuadro que muestra un número distinto del
que se usó para decidir no sirve para auditar** — ahora los dos leen la misma.

### E2.v — «¿Qué es lo que aplicaría a mano?» (2026-08-17)

BPOA8 salía en `revisar` con la TEA **clavada** (7,08% contra 7,0814%), la
duration clavada (2,1285 contra 2,1266) y una paridad 0,90% distinta. El
veredicto decía *«se puede aplicar A MANO»* y el user preguntó lo obvio: **qué
era lo que tenía que aplicar a mano.** Nada. No faltaba ningún dato.

Tres cosas mal, las tres del mismo tipo — **el agente sabía la respuesta y no la
estaba usando**.

**1. La diferencia de paridad es el interés corrido, y es por definición.**

```
nuestra  = precio / residual                 (engines/curvas.py)
1816     = precio / (residual + devengado)   (valor técnico)
```

La nuestra da SIEMPRE un poco más alta. Medido en los dos casos que teníamos:
GD46 0,24% con un cupón de 2,5%, BPOA8 0,90%. El umbral de 0,5% estaba tratando
como sospecha algo que es aritmética.

El fix es una **cota, no una predicción**: el devengado nunca supera un cupón
entero sobre el residual vivo, y eso se deriva del cuadro (`cota_devengado`).
Calcular el devengado exacto obligaría a elegir una convención de días y a
acertarle a la de 1816 — o sea a inventar una hipótesis nueva para tapar un
problema que se resuelve sin ella. Y no se pierde poder de detección: un error de
escala mueve la paridad 100 o 1.000 veces, dos órdenes de magnitud arriba de
cualquier cupón.

**2. La duration estaba a la vista y no votaba.** El cuadro ya mostraba «nuestra
2,1285 · 1816 2,1266» con la leyenda *«depende solo del cuadro y las fechas: si
difiere, el cronograma que bajamos no es el mismo»* — y el paso decidía sin
mirarla. Ahora es el **segundo testigo**, y hace falta porque mide otra cosa:

| | detecta | es ciega a |
|---|---|---|
| **paridad** | la ESCALA (×100, ×1.000, Σ mal) | las fechas |
| **duration** | el CRONOGRAMA (cupones, fechas) | la escala |

Cada una es ciega justo donde la otra ve. Con las dos, una duration que no
coincide **bloquea aunque la paridad esté perfecta** — un poder de detección que
antes no existía.

**3. «A mano» solo si hay algo que hacer a mano.** Un `revisar` con `aviso` pide
CARGAR un dato (el CER de emisión); uno sin aviso es un juicio. El veredicto los
mezclaba en un solo texto. Ahora el que pide un dato dice cuál y que va a AVISOS;
el que es juicio dice *«no falta ningún dato, pero conviene mirar…»*.

### E2.w — El aviso se completa DONDE se lee (2026-08-17)

Pedido del user: *«el aplicar a mano como el del CER estaría bueno que pase a
aviso y quede con el campo a completar desde ahí MISMO… escribo el valor y ya
entiende cómo guardarlo en la base»*.

Tenía razón y era la mitad del trabajo hecha: el aviso decía «falta el CER de
emisión» y te mandaba a Manager → Títulos a buscar el bono. El agente hacía el
95% y dejaba el 5% a tres clics.

Ahora cada aviso viaja con **`campo`** —label, tipo y ayuda— y la fila trae su
input. `POST /api/ia/av-agent/aviso/completar` escribe el valor y cierra el aviso
en el mismo acto. Dos cosas que **no** hace, a propósito:

- **No inventa el campo.** Solo se escribe lo que está en `_CAMPO_AVISO` (hoy
  `cer_emision` y `emisor`); una clave desconocida devuelve error en vez de
  escribir algo parecido, y el aviso queda para cerrarlo a mano.
- **No confía en que escribió.** Después del `UPDATE` relee con el **mismo
  predicado** de `_COND_AVISO` —el que la lista usa para decir `ya_cargado`— y si
  el dato no quedó, **no cierra el aviso**. Cerrar sin verificar es exactamente
  el «marcado hecho + dato ausente» que `ya_cargado` existe para cazar.

`_CAMPO_AVISO` vive PEGADO a `_COND_AVISO` porque son las dos caras del mismo
dato: uno lo escribe, el otro verifica. Separarlos es cómo se consigue un aviso
que se puede completar pero nunca se marca cargado.

### E2.x — El dato se pide ANTES de aplicar, no después (2026-08-17)

E2.w puso el input en la tab AVISOS: cargás el CER **después** de dar de alta. El
user apuntó a lo que faltaba:

> *«¿No podría ser ACÁ MISMO interactivo y que me pida el CER de emisión para
> continuar? Y que rehaga la simulación con ese dato y si va todo bien ya lo
> aplique con eso.»*

Tiene razón, y la diferencia no es de comodidad: **es aplicar a ciegas contra
aplicar viendo la tasa.** El flujo viejo era aplicar → ir a AVISOS → cargar el
número → y recién ahí enterarse de si el bono cerraba. El nuevo es escribir el
número, ver la TEA, y aplicar con el dato adentro.

**Cómo funciona.** Un paso puede declarar **`pide`** —campo, label, tipo, ayuda—
y la cadena renderiza su input ahí mismo. Lo tipeado viaja **igual a SIMULAR y a
APLICAR**, así que lo que se aplica es exactamente lo que se vio simulado; dos
payloads distintos harían que el bono naciera con insumos que nadie miró.

Y el bono **nace CON el dato**: `aplicar()` lo pasa a `simular()`, el paso deja de
estar en `revisar` y **no se genera el aviso** — porque ya no falta nada. El aviso
de E2.w sigue existiendo para el que igual prefiere aplicar y cargarlo después.

**El valor tipeado gana sobre el derivado, nunca al revés.** Si el user lo
escribe, lo sacó del prospecto o del BCRA — de una fuente que el sistema no
tiene. Un valor inválido (≤ 0) se ignora en vez de romper la simulación, y el
paso lo dice: la línea deja de atribuirle el número a 1816 cuando lo puso una
persona.

**El patrón general que queda**: el pre-flight ya no es solo un informe, es un
**formulario que sabe qué le falta**. `pide` es genérico —hoy lo usa el CER, mañana
cualquier paso que necesite un dato que ninguna fuente publica— y como el estado
del input vive por hallazgo, simular dos bonos seguidos no filtra el número de uno
al otro.

### E2.y — El chequeo que no podía detectar nada (2026-08-17)

El user, mirando el TZXA7: *«la serie del CER tiene datos viejos, o sea que es
cualquiera que no lo detecte»*. Tenía razón, y **el chequeo existía**. Lo que no
existía era su capacidad de detectar.

`salud.py` tenía este contrato:

```python
{"id": "dato:macro.series_macro", "titulo": "Series macro (CER/dólar/tasas)",
 "tabla": "macro.series_macro", "columna": "fecha", "max_dias_habiles": 3}
```

Y se evaluaba así: `SELECT MAX(fecha) FROM macro.series_macro` — **sin filtrar por
`serie`**. Esa tabla tiene DOLAR, CER, BADLAR, TAMAR, RiesgoPais e Inflación
mezcladas. **Un MAX sobre N series independientes responde por la más fresca**: con
el dólar actualizándose todos los días, el CER podía estar congelado hace meses y
el tablero seguía en verde. El chequeo no estaba roto — estaba **estructuralmente
incapaz** de ver lo que decía vigilar.

Es, otra vez, **un agregado que tapa el detalle**: la sexta aparición del mismo
anti-patrón en una semana. Y es la peor versión, porque un agregado que tapa el
detalle **en un chequeo de salud** no solo esconde el problema: **certifica que no
lo hay**.

**El fix**: los contratos aceptan `filtro` y `macro.series_macro` se declara **una
vez por serie crítica** (CER, DOLAR, BADLAR, TAMAR), cada una con su tolerancia y
su propio id — así se puede silenciar el ruido de una sin apagar la alerta de las
otras. Se declaran explícitas y no agrupando por `serie`: agrupar generaría un rojo
por cada serie mensual, discontinuada o experimental que alguien haya escrito
alguna vez.

**Lo que el agente todavía no podía decir.** Su mensaje era *«la serie CER no
llega hasta 2025-11-28»*, que confunde dos problemas distintos: la serie
**ATRASADA** (dejó de correr un job) y un **HUECO** (un día que no se ingestó).
Ahora el mensaje dice hasta dónde llega la serie y cuál de los dos es — se
responde solo. Y `scripts/diag_cer_serie.py` mide el resto: rango, atraso o
forward, huecos agrupados en tramos, y qué ve `cargar_cer(dias=4000)`, que es la
ventana del motor y podría ser más chica que la serie.

**La regla que queda**: *un chequeo de frescura sobre una tabla que mezcla series
tiene que declarar CUÁL mira.* Un MAX sin filtro no es un chequeo laxo — es un
chequeo que no puede fallar.

### E2.z — El dato ESTABA: el mensaje culpó a la fuente equivocada (2026-08-17)

El user, con la captura de `macro.series_macro` abierta: *«JUSTAMENTE TE ESTOY
MOSTRANDO QUE PARA ESA FECHA HABÍA. Sabés la fecha de emisión, tiene que buscar
10 días para atrás, es sencillo.»*

Tenía razón en todo, y **yo agravé el problema en el turno anterior**: en vez de
leer el código, lo mandé a correr un diag para medir si la serie estaba vieja. La
respuesta estaba en el repo.

**La aritmética, congelada en test:**

```
emisión              2025-11-28   (hábil)
T−10 hábiles    =    2025-11-12   (hábil)   ← 16 días corridos: 2 findes + 2 feriados
CER 2025-11-12  =    651,89806…              ← CARGADO en la base
```

**Dos errores encadenados, los dos míos.**

**1. El mensaje mentía la fecha.** Interpolaba la fecha de EMISIÓN y la etiquetaba
`(T−10 hábiles)`:

```python
f"la serie CER no llega hasta {fecha_emision[:10]} (T−10 hábiles) — …"
```

O sea que nombraba **un día que la función nunca buscó**. Cualquiera que fuera a
verificarlo —el user, y después yo— miraba la fecha equivocada. Un mensaje que
nombra mal su propio insumo no manda a mirar el lugar equivocado por casualidad:
lo hace siempre.

**2. Un `None` con tres causas colapsado en una sola frase.**
`get_cer_liquidacion` resuelve el T−10 **indexando `mercado.dias_habiles`** y
devuelve `None` si esa tabla no llega diez hábiles antes de la fecha. El llamador
leía ese `None` como *«no hay CER»* — **una conclusión que la función nunca
afirmó**. Sin calendario, sin ese día en la serie y error de lectura daban todos
el mismo texto, y ese texto acusaba siempre a la serie.

**El fix.** Los eslabones se resuelven por separado y el mensaje nombra **las dos
fechas**: `emisión 2025-11-28 → T−10 hábiles = 2025-11-12, y …`. La fecha sale
primero del calendario oficial —el mismo que usa el motor, así el número no puede
diferir del suyo— y **solo si ese no alcanza**, del cálculo puro
(`calendario.restar_habiles`, sobre `holidays.Argentina`), diciendo por cuál vía
salió. Porque **no poder leer la tabla no es lo mismo que no poder saber qué día
era**: el calendario hábil argentino se calcula, no se consulta.

**Las dos reglas que quedan:**

- *Un mensaje de error tiene que nombrar el insumo que la función realmente usó.*
  Si dice una fecha y busca otra, convierte a todo el que lo lea —incluido el que
  lo escribió— en alguien que verifica el dato equivocado.
- *Un `None` no es un diagnóstico.* Si una función puede devolver `None` por tres
  razones, el que lo recibe no puede elegir una y escribirla como si fuera un
  hecho. O se distinguen las causas, o el mensaje dice «no pude», no «no hay».

Y una tercera, para mí: **antes de pedir una medición, leer el código.** El user
había puesto la evidencia en pantalla; lo que faltaba era abrir dos funciones.

## E3 — La lista se puede LIMPIAR y el segundo hallazgo se acciona (2026-08-17)

### E3.a — «No me interesa», para cualquier hallazgo

Pedido del user: *«¿cómo podríamos hacer para ignorar algunos, tipo decir "no me
interesan", así no vuelven a aparecer?»*. El mecanismo **existía** y no servía,
por dos razones:

1. **Solo se disparaba contestando una PREGUNTA** del agente (`falta:<TICKER>`).
   Si el agente no te había preguntado por ese bono, no había forma de descartarlo.
2. **El filtro se pasaba por parámetro solo a `detectar_faltantes`.** Los otros
   tres detectores seguían reportando un ticker ya descartado.

Ahora hay un botón **IGNORAR en toda fila** y el filtro se aplica **una vez, sobre
la lista completa** — que es lo que hace imposible que un detector nuevo se olvide
de mirarlo. `tickers_ignorados()` es el único lector, y lo llaman **relevar Y
leer**: sin lo segundo, apretar el botón dejaba la fila en pantalla hasta la
próxima corrida (~29 créditos), que es la regla de E2.r otra vez.

**Por TICKER y no por (ticker, tipo)**: si un papel no interesa, no interesa en
ninguna de sus formas. En un `hueco_de_curva` el `ticker` es el **AJUSTE**
(`BADLAR`) y se ignora igual: esta tabla es la lista de *cosas que no quiero ver*,
y la identidad de un hallazgo es su `ticker`, signifique lo que signifique para su
tipo.

### E3.b — `flujos_vacios` deja de ser un comentario

DICP y PARP decían *«Sin cronograma de pagos; 1816 lo tiene y se puede
completar»* — y ahí morían. El único hallazgo que afirmaba que el agente podía
resolverlo y no ofrecía resolverlo.

**Es el alta al revés, y esa inversión es todo el diseño.** En un alta los ejes
los DERIVA el agente de la curva de 1816, porque el bono no existe. Acá el bono
existe, la vista ya lo muestra, y **sus ejes los cargó la mesa**: son la verdad y
no se vuelven a derivar. `rama_calculo(doc)` sale del propio doc.

Y por eso el cotejo pesa **más** que en un alta: se le va a escribir un cronograma
a un bono vivo. Si ese cuadro está mal, el bono pasa de *«sin TEA»* a *«con una
TEA equivocada»*, que es estrictamente peor. Se reusa el **mismo `_cotejo_tea`**
—paridad, duration y la cota del devengado de E2.v— porque un cuadro escrito por
esta puerta tiene que pasar el mismo examen que uno escrito por el alta. Es
exactamente lo que pidió el user: *«lo que hay que chequear es si con el flujo que
agregaríamos y nuestro modelo nos da una TEA y esos datos como a 1816»*.

**La garantía de que no pisa nada no es el cuidado, es el payload.** El UPDATE es
un merge sobre el blob (`data || parche`) y el parche contiene **solo el
cronograma**: emisor, curva, símbolo, ejes y `cer_emision` ni siquiera están ahí,
así que no se pueden pisar por accidente. Un paso de la cadena lo enumera —«qué se
va a escribir»— porque la diferencia entre *confiá* y *mirá* es que estén listados.

La cadena es más corta que la del alta a propósito: los ejes, la curva, el símbolo
y la especie ya están resueltos, y chequearlos sería teatro. Lo que se chequea es
lo que puede salir mal **al escribir en un bono vivo**. Y si el bono ya tiene
cronograma, no se re-escribe: se dice que el hallazgo quedó viejo.

### E3.c — TIPO y REGLA: el botón que no aparecía y no avisaba (2026-08-17)

El IGNORAR funcionó de una. El de completar el cronograma **no apareció**, y no
dio ningún error. La causa es de una línea:

```
tipo  = "sin_flujo"        ← agrupa la sección de la pantalla
regla = "flujos_vacios"    ← dice CUÁL chequeo lo disparó
```

Yo escribí en el front `h.tipo === "flujos_vacios"` — comparé contra la **regla**
creyendo que era el **tipo**. Y como es un `&&` en JSX, no hay error: el botón
simplemente no se renderiza. **Silencio total.**

Es el mismo par que ya nos mordió en E2.o (RAMA vs CURVA): **dos vocabularios que
se parecen, conviven en la misma estructura, y confundirlos no rompe nada — solo
deja de hacer algo.** Los bugs que no fallan son los que cuestan una vuelta entera.

**El fix no es corregir el string.** Es que el front no tenga strings de tipos: la
acción la decide el backend en **`ACCION_POR_TIPO`** y **viaja en el hallazgo**
(`h.accion`). El front solo lee `h.accion === "alta" | "flujos"`.

Tres propiedades que salen gratis de esa decisión:

- **El que sabe si un hallazgo es accionable es el que sabe resolverlo.** El front
  no tiene por qué conocer la taxonomía de detectores.
- **Se deriva en la LECTURA, no se persiste**: el día que un tipo se vuelva
  accionable, los hallazgos ya guardados lo heredan solos.
- **Sumar una acción es una línea del backend** y aparece en la pantalla sola.

Y queda un test que el front no podía tener: que las claves de `ACCION_POR_TIPO`
sean **tipos que los detectores realmente emiten**. Si alguien vuelve a escribir
una regla ahí, falla el test en vez de fallar el botón.

**La regla general**: *si el front tiene escrito a mano un string que el backend
también conoce, ese string es el bug esperando.* No porque esté mal hoy — porque
nada los obliga a seguir de acuerdo mañana.

### E3.d — DICP y PARP: un doc de `mercado.curvas` NO es el blob `data` (2026-08-17)

La primera simulación real de un `flujos_vacios`. Las dos cadenas volvieron con
el cuadro perfecto y **sin TEA**, con estos números:

```
DICP   duration nuestra 7,3781   ·  1816 dice 3,2512   ·  paridad —
PARP   duration nuestra 12,3808  ·  1816 dice 6,5497   ·  paridad —
ejes cargados por la mesa (None · None · None)
```

El user lo leyó como lo que era: *«no me termina de convencer… ya tenemos bonos
valuados… claramente acá hay un problema con la forma en la que se guarda el
cashflow»*. Tenía razón en que algo estaba mal; el cashflow no era.

**Lo primero fue medir, no opinar** (REGLA #2). DICP vence 2033-12-31: son 2.695
días → 2.695/365 = **7,3836**. PARP vence 2038-12-31: 4.519 días → **12,3808**,
exacto. Esos no son duration de nada: son **los años al vencimiento**, o sea el
valor que devuelve el motor cuando sale por su puerta de emergencia:

```python
# engines/curvas.py, rama CER
if not cer_emision or cer_emision <= 0:
    resultado["duration"] = round(dias_a_vto / 365, 4)
    return resultado          # ← sin error, sin log, sin nada
```

Faltaba el **CER de emisión**. Pero el encabezado de la cadena no decía eso:
decía *«revisar la escala del flujo o la pata»* — mandaba a mirar el cuadro, que
estaba impecable. Y ahí está la causa raíz, que es de LECTURA:

> **Un doc de `mercado.curvas` no es su blob `data`.** Los EJES (`emisor_tipo` ·
> `moneda_eje` · `ajuste` · `ajuste_alt` · `ley`) viven en **columnas**, y
> `core/curvas_sql` los mezcla encima del blob (`_COLS_FUERA_DEL_BLOB`).

Mi `_doc_de_curvas` hacía un `SELECT data` y nada más. Con eso `ajuste` llegaba
en `None`, y a partir de ahí **todo mintió en cascada**:

1. la pantalla mostró `DICP · · ·` y `(None · None · None)` — el síntoma estaba
   a la vista y se leía como un detalle cosmético;
2. `sin_cer`, que decide el mensaje, evalúa `ajuste == "cer"` → con `None` dio
   **False**, así que el encabezado acusó a la escala en vez del CER;
3. el motor, sin `cer_emision`, salió por la puerta de emergencia.

**El fix es de una línea y de fondo a la vez**: `_doc_de_curvas` lee por
`curvas_sql.cargar_todos()` — **la misma fuente que lee el motor**. Dos lectores
distintos del mismo dato son dos verdades que se separan solas; la única defensa
que escala es que haya un solo lector.

**Lo segundo: el dato faltante se pide ACÁ MISMO.** El CER de emisión pasó a ser
un paso `pide` de la cadena de flujos, igual que en el alta (E2.x): se tipea, se
re-simula con ese número, se ve la TEA, y recién ahí se aplica. Y se escribe con
él — es la **única excepción** a «acá solo se escribe el cronograma», explícita y
detrás de un guard: solo si el user lo tipeó **y** el bono no lo tenía. Sin esa
excepción el bono quedaba con cuadro y sin tasa, que es la mitad del trabajo.

**Lo tercero, y es lo que el user pidió mirar de frente**: *«no usar TODOS LOS
CUPONES si ya hay un montón que se pagaron»*. Verificado en el motor: las cuatro
ramas filtran `fecha_flujo(f) > fecha_settlement`. **El motor ya lo hacía.** Lo
que faltaba no era el filtro: era **decirlo**. 1816 manda el cronograma COMPLETO
desde la emisión (medido en GD46: 51 cupones desde 2021-07-12, Σ = 100,000012),
así que un bono de 2004 trae decenas de cupones cobrados y ver «60 cupones» sin
aclaración da a entender que se valúan los 60. Ahora la cadena parte el número:

```
60 cupones (54 ya pagados · 6 futuros) — el cuadro se guarda COMPLETO
(es el del bono) y el motor valúa solo los futuros.
```

Y si **no queda ningún cupón futuro**, el paso pasa a **BLOQUEA**: ese bono ya
venció, escribirle el cuadro no lo arregla y aplicar sería ensuciar el master.

**Cuarto, de yapa: el front tenía la cadena escrita DOS veces.** `AccionAlta` y
`AccionFlujos` eran el mismo componente con tres strings distintos, y por eso el
bloque que pide el dato faltante existía **solo en el alta**: la rama de flujos
mandaba su `pide` y no se renderizaba nada. Misma familia que E3.c — falla en
silencio. Ahora es **un** `AccionCadena` parametrizado por `modo`, y lo que
cambia son las etiquetas.

**Las reglas que deja:**

- *Si el simulador y el motor no leen por la MISMA función, tarde o temprano ven
  bonos distintos.* No es hipotético: acá el simulador vio un bono sin ajuste.
- *Un número redondo sospechoso tiene que medirse antes de teorizar.* 12,3808 no
  era «una duration rara»: era `4519/365`, y eso apuntó al bail-out en un paso.
- *Cuando el sistema hace lo correcto pero no lo cuenta, el user tiene razón en
  desconfiar.* El filtro de cupones pagados ya existía; el problema era que la
  pantalla no daba forma de saberlo.

### E3.f — DICP: el divisor del cuadro CER es el RATIO, no la Σ (2026-08-17)

**El caso.** PARP cerró exacto contra 1816 (TEA 0 bps, duration idéntica) y DICP
no: TEA 3,91% contra 9,25%, duration 3,4756 contra 3,2512. La única diferencia
estructural entre los dos: **PARP todavía no amortizó nada** (sus 20 cuotas
arrancan en 2029) y DICP amortiza desde 2024.

**Lo que se midió antes de tocar nada** (`scripts/diag_cer_amortizado`):

```
PARP  Σ/ratio = 100,0025 por 100 VN → cada amortización 5,000126%  ← de manual
DICP  Σ/ratio = 118,3040 por 100 VN → pero las 20 valdrían 126,9969
```

**La causa: 1816 manda cada flujo en pesos ajustados por el CER DE SU PROPIA
FECHA** — los pagados en pesos de cuando se pagaron, los futuros en pesos de hoy.
Nuestro divisor era la Σ del cuadro COMPLETO, o sea **sumar pesos de 2024 con
pesos de 2026**. Y encima DICP capitalizó interés, así que su total a amortizar no
es 100 del VN original sino 126,99: forzarlo a 100 dividiendo por Σ es un segundo
error montado sobre el primero.

Las tres conversiones candidatas, corridas contra los indicadores de 1816 **al
mismo precio**:

| conversión | TEA | duration |
|---|---|---|
| **1816** | **9,2475%** | **3,2512** |
| ÷ Σ (lo que hacíamos) | 3,9132% | 3,4830 |
| ÷ residual futuro (80,51%) | 10,8880% | 3,1928 |
| **÷ ratio de CER** | **9,2268%** | **3,2591** |

21 bps y 0,24% de duration, contra 530 bps del anterior. Y el testigo que no deja
lugar a dudas: con el ratio, cada cuota de PARP da **5,000126%** — el número de
prospecto.

**El invariante que deja escrito**, y que es la razón de fondo:

> `monto_flujo_cer(f) × ratio` tiene que reproducir el importe en pesos que
> publica 1816 — hoy y con cualquier CER futuro.

Por eso el divisor es el ratio y no una Σ: **lo que se guarda tiene que ser el %
del VN ORIGINAL, que no depende del CER.** Una Σ es una propiedad del cuadro que
bajamos; el ratio es la unidad en la que ese cuadro está escrito.

**Tres consecuencias:**

1. **El CER de emisión pasa a ser BLOQUEANTE** en completar-cronograma. Antes era
   «revisar»: se escribía el cuadro y el CER quedaba pendiente. Ya no se sostiene
   — sin ese número el cuadro **ni siquiera se puede convertir**, y escribirlo
   igual dejaría un cronograma en una escala inventada: se ve cargado y valúa mal,
   que es peor que no tenerlo.
2. **El orden importa**: el CER se resuelve ANTES de convertir. Antes daba lo
   mismo porque el divisor no dependía de nada.
3. **Un paso nuevo, DIVISOR**, con la Σ resultante en % del VN original. Es
   auditable de un vistazo: 100 para un bono común, >100 para uno que capitalizó
   (DICP: 118,30). La Σ dejó de ser una constante decorativa y pasó a decir algo
   del bono.

**Y lo que NO se tocó, a propósito**: la paridad del motor. Medido, el valor
técnico implícito de 1816 para DICP es 56.094 contra los 56.140 de nuestro
`100 × ratio` — **0,08%**. O sea que 1816 también calcula la paridad CER contra el
VN ORIGINAL, no contra el residual (al revés que en soberanos, E2.u). Lo que
quedaba de diferencia era clean contra dirty.

**RIESGO GEMELO, declarado y no medido**: un dólar-linked se ajusta por A3500
igual que un CER por CER, así que su cuadro tiene la misma estructura. No se
cambió porque los DL que probamos (D10Y7, D30O6) **no habían amortizado nada** —
justo el caso en que los dos divisores coinciden y el problema no se ve. Queda
marcado en el código, en el lugar exacto.

**La regla general**: *cuando un cuadro viene expresado en una unidad que se
mueve, la Σ de sus filas no es una escala — es una suma de cosas distintas.* El
divisor tiene que ser la unidad, y la unidad hay que pedirla, no inferirla.

### E3.g — La paridad no puede desmentir a la TEA + la duration (2026-08-17)

Con el divisor arreglado (E3.f), DICP volvió así:

```
TEA (al MISMO precio)   nuestra 9,2475%   ·  1816 9,2475%   → 0 bps
duration                nuestra 3,2512    ·  1816 3,2512    → 0,00%
paridad                 nuestra 86,57%    ·  1816 90,19%    → 4,01%
```

…y la cadena **BLOQUEABA**: *«se contradicen → otro valor técnico, o sea otro
cronograma»*. Eso es **aritméticamente imposible**. La TEA a un precio dado es
una función del cronograma y de las fechas; dos cuadros distintos no pueden dar
la misma tasa al mismo precio **y** la misma duration al cuarto decimal.

**El error era de JERARQUÍA, no de umbral.** La paridad era el juez y la duration
el testigo — al revés de lo que corresponde:

| | ¿de qué depende? | ¿puede desmentir al cuadro? |
|---|---|---|
| **TEA al mismo precio** | flujos + fechas | sí |
| **duration** | flujos + fechas | sí |
| **paridad** | flujos + **una DEFINICIÓN** (qué precio arriba, qué abajo) | **no sola** |

La paridad es la única de las tres que depende de una convención además de los
datos. Ponerla de juez la convierte en un veto sobre evidencia más fuerte.

**Qué difería, con la cuenta hecha.** 1816 divide su precio **CLEAN** por el
valor técnico; nosotros dividimos el que **OPERA** —su `precioDirty`, que para
DICP es exactamente nuestro 48.600— por VN × ratio:

```
DICP   86,57 × (50.589,80 / 48.600) = 90,11  contra 90,19  → 0,08%
PARP   63,77 × (35.419,08 / 35.800) = 63,09  contra 63,34  → 0,25%
```

O sea: **el valor técnico de ellos y el nuestro son el mismo número.** Lo que
cambiaba era el numerador. La cadena ahora **muestra esa cuenta** en vez de
interpretar — el que audita la rehace en la pantalla.

**La prueba necesita las dos patas.** TEA sin duration no alcanza (una escala mal
puesta puede compensar), duration sin TEA tampoco. Con las dos, el cronograma
está confirmado y lo que quede es definición. Hay contraprueba en el test: con la
duration desalineada vuelve a BLOQUEAR.

**La regla general**: *un chequeo que depende de una convención no puede vetar a
uno que depende solo de los datos.* Cuando dos controles se contradicen, gana el
que tiene menos supuestos — y si no se sabe cuál es, eso es lo que hay que
averiguar antes de elegir cuál bloquea.

### E3.h — El agente deja de necesitar a 1816 para diagnosticar (2026-08-17)

Planteo del user, y era el diseño lo que estaba mal: *«hay casos como PARIDAD
FUERA DE RANGO que **no necesitan ir a 1816 a consultar** — hay casos donde este
agente podría debuguear internamente, ya que hay precio y ya está el flujo
cargado»*.

Tenía razón. **El agente sabía hacer UN solo arreglo** —traer el cronograma de
1816 y pisar los flujos— así que las cinco reglas de tasa pasaban por la misma
cadena, y sus dos puertas de 1816 (`cuadro` y `precio`) la bloqueaban entera. Con
rate limit, un domingo, o con un bono que 1816 no cubre, la pantalla no decía
**nada**: ni siquiera lo que sale de una división.

**Medido antes de tocar una línea** (`scripts/diag_av_agent_arreglos`, corrida del
2026-08-17 sobre los 38 hallazgos reales):

| causa | hallazgos | ¿necesita 1816? |
|---|---:|---|
| `moneda_flujo` contradice a los ejes | 14 | **no** |
| sin ejes y sin ficha en el catálogo | 9 | **no** |
| cuadro en nominales de la emisión | 6 | **no** |
| falta el CER de emisión | 4 | **no** |
| sin espejo en `portafolio.assets` | 2 | **no** |
| el cuadro usa el campo de la otra rama | 1 | **no** |
| precio 0 / stale | 2 | **no** |
| bug del motor (paridad CER) | 1 | **no** |

**38 de 38 se resuelven con datos que ya están en la base.** Ninguno necesitaba la
red: 1816 no era el insumo, era una costumbre.

#### La causa #1 — dos vocabularios para el mismo hecho

`rama_calculo` se migró a los EJES el 2026-08-16, pero **la rama ON sigue
despachando por `moneda_flujo`** (`engines/curvas.py:665`), que es un campo aparte
y se carga a mano. Mientras coinciden no pasa nada; cuando divergen, **el motor
calcula con uno y la vista clasifica con el otro**, y no salta ningún error.

Lo confirma la aritmética de producción:

```
OLC3O   (137.280 / A3500) / 146.300 × 100 = 0,065   = el 0,06 que muestra
        → `moneda_flujo`='DL' anda; el problema SÍ es el cuadro
LOC6O   156.570 / 100 × 100 = 156.570
        → el motor NO convirtió nada. Pero el símbolo termina en "O" y
          `precio_soberano_a_usd` lo habría dividido por MEP sin problema:
          **la pata no está mal, `moneda_flujo` sí**
```

Cuatro bonos tenían **`HD`**, que es el vocabulario de la CARTERA
(`portafolio.assets`), no el del motor. Y acá está lo grave: **el `else` de la
rama ON no valida nada**, así que una palabra que no reconoce se convierte en ARS
en silencio — sin error, sin log, sin celda vacía. Solo una paridad de 156.570 que
parece un dato.

> **Regla**: el arreglo se elige por la causa que está **aguas arriba**. Con
> `moneda_flujo` mal, todo lo demás que se mida está medido en la unidad
> equivocada — proponer «cambiá la pata» ahí es pisar un dato sano para tapar el
> síntoma de otro. Ordenarlo mal fue el error de la primera versión de este
> diagnóstico, y por eso el ORDEN es parte del contrato (`diagnosticar_local`).

#### El defecto se detecta, no se espera al síntoma

Las demás reglas miran una MÉTRICA fuera de rango, así que solo ven el error
cuando es lo bastante grande **y ese día hubo precio**. El censo sobre todo
`mercado.curvas` mostró el agujero: **30 de 140 bonos** de la rama ON tienen
`moneda_flujo` contradiciendo a sus ejes, y solo **8** habían disparado un
hallazgo. Los otros 22 estaban igual de mal valuados y **no aparecían en ninguna
pantalla**.

La regla nueva (`moneda_flujo_contradice`) mira los dos campos del mismo doc, así
que **no necesita precio, ni snapshot, ni 1816**: es cierta un domingo y con la
API caída, y no se puede disparar por un valor viejo pegado en el snapshot.

#### La verificación también es local

Sin cotejo contra 1816 hace falta otra prueba de que el cambio mejora algo, y no
puede ser «yo creo». Se corre **el mismo motor que valúa en producción** con el
bono parchado y se exige que **la métrica que disparó el hallazgo vuelva al
rango** — y que antes estuviera afuera. Es un control tan duro como el de 1816,
cuesta cero créditos y se puede correr un domingo. Si la paridad no vuelve, el
diagnóstico estaba mal y **no se escribe nada**.

#### Lo que el agente NO arregla, y lo dice

`paridad_del_motor` es la única causa cuyo arreglo no es un dato: `curvas.py:457`
arma el valor técnico del CER con `valor_nominal` (estático, 100) en vez del
residual VIVO, así que un bono que ya amortizó el 80% muestra la paridad 5 veces
más chica (TX26: 20,07 contra 100,4 real). Es la misma lección de E2.u, que se
arregló en la rama ON y quedó pendiente en la CER. **El bono está bien**, y el
agente tiene que poder decirlo en vez de proponer que se lo toque.

#### Dos cosas más que salieron del mismo diag

- **Fósiles en el snapshot.** `mercado.market_snapshot` es un upsert PARCIAL:
  cuando el motor sale por una puerta de emergencia (CER sin índice → `:439`,
  rama `otros` → `:733`, XIRR fuera de rango) escribe **solo `duration`**, y la
  TEA y la paridad viejas se quedan ahí **sin fecha propia**. CO3D7 (417,09),
  PMA28 (200,25) y TMF27 (4.789,16) muestran paridades que el motor **no
  produce hoy**: el hallazgo lo dispara un valor de otra época.
- **El precio tenía tres fuentes locales y se usaba una.** `_precio_local` va
  snapshot → `mercado.snapshots_cierre` → **la evidencia congelada del propio
  hallazgo** (que ya viajaba hasta el front y se tiraba para volver a leer en
  vivo). Y un `last_price` de **0 no es un precio**: DHSGO entraba como válido.

#### Qué NO cambió

El alta (`simular`/`aplicar`), completar cronograma (`simular_flujos`/
`aplicar_flujos`), los detectores, la vista, ME PREGUNTA, AVISOS, IGNORAR, el
libro de acciones, los cinco estados, `_paso` y `_veredicto` quedaron **intactos**
— se reusan tal cual. El camino de 1816 sigue existiendo para lo único que
realmente lo necesita: traer un cronograma que no tenemos.

## Changelog

- **2026-08-19 — la superficie HTTP deja de ser un punto ciego, y el agente
  PRUEBA que los permisos sean reales** (§0.s). Medido: el tooling de seguridad
  veía **37 de 541 rutas** y pasaba en verde — auditaba el 7% y afirmaba que
  estaba todo bien. Y `gen_mapa_app`, el que se creía correcto, **duplicaba el
  prefijo en 395 de 541 paths**. Ahora hay **una sola** forma de recorrer la
  superficie (`api/superficie.py`) y las tres herramientas delegan ahí. Al
  destaparlo aparecieron 13 escrituras «sin gate»: 12 eran allowlists que el test
  no sabía reconocer, **la 13ª era real** (`/api/avisos` sin bearer) y se
  arregló — de paso el rechazo del invitado pasó de ser un `if` adentro del
  handler a una dependency, porque un `if` no lo puede auditar nadie. Y lo nuevo:
  el agente **prueba de verdad**, sin credenciales, contra la URL pública, con el
  invariante «nada contesta distinto de 401/403». Solo GET, solo rutas propias,
  con throttle, jamás una escritura — y declarando siempre qué capa cubrió (**no
  cubre Vercel**). 35 habilidades, 25 sin IA.

- **2026-08-19 — EL CONTEXTO: el agente conoce la base sin que nadie se lo
  escriba, y se entera si un motor se cae** (§0.r). *«Que sepa exactamente cada
  tabla que hay y cómo funciona en cuanto a los datos… que no dependa de un git
  pull, que no dependa de cosas estáticas»* (user). **No hay ninguna lista**: el
  inventario sale de `pg_catalog`, la columna de fecha de `information_schema` y
  **la cadencia se MIDE** mirando la distribución de esa columna (mediana, no
  promedio; sobre las últimas escrituras, no todas). Siete clases de cadencia y
  un veredicto de frescura por tabla — cubre las ~190 que eran punto ciego, y las
  8 con contrato declarado las sigue mirando `salud.CONTRATOS`, que es más
  estricto **porque el aprendido se acostumbra al problema si el job lleva días
  roto**. Los MOTORES: las 53 piezas de `diagnostico_registry` ya tenían cadencia,
  ventana y umbral y el agente no las leía — ahora las lee y canta las rotas
  **dentro de su ventana** (fuera de rueda un motor no está caído, está apagado).
  Y el explicador **«¿está todo funcionando bien ahora?»**, que junta motores +
  tablas + velocidad en una línea. 33 habilidades, 24 sin IA.

- **2026-08-19 — el agente entiende la BASE DE DATOS y la LATENCIA** (§0.q). Foto
  diaria del tamaño de cada tabla (`jobs/db_tamano`, 23:30 UTC) que guarda **solo
  hoy y ayer** y purga en el mismo INSERT: lo que informa no es el tamaño sino el
  DELTA —qué apareció, qué creció y por cuánto, qué desapareció—. El corte se
  auto-calibra sobre el tamaño real de la base en vez de un umbral en MB que
  envejece. Y la LATENCIA: la pantalla no servía porque **un ranking muestra lo
  LENTO, no lo ANORMAL** (arriba estaban una llamada al LLM y un proveedor
  externo, las dos bien y las dos ahí mañana también). Ahora cada endpoint se
  compara **contra sí mismo** con cuatro guardas contra el falso positivo —
  volumen mínimo, historia mínima, relativo Y absoluto, y mediana en vez de
  promedio para que un pico previo no tape el problema. La tab LATENCIA se
  elimina; la tabla y el middleware quedan. Las dos capacidades entraron a SKILLS
  solas por la ley de §0.o, y las dos son función pura: 30 habilidades, 24 sin IA.

- **2026-08-19 — la tab SKILLS y la LEY del registro único; el agente admin-only
  pero sus avisos llegan a cualquiera** (§0.o y §0.p). *«Por ley y regla, todo lo
  nuevo que se agregue de funcionalidad o habilidad tiene que quedar en esta
  tab»* (user). El catálogo **se deriva** de los registros reales —detectores,
  controles, explicadores, acciones—: una skill aparece por existir, y donde no
  puede derivar hay un test que exige la descripción (y otro que falla si se
  describe algo que ya no existe). Cada una declara si usa el modelo con TRES
  valores y no un booleano, porque `opcional` —la parte que resuelve es una
  función— es la categoría más común y la que se cuenta mal para los dos lados.
  **26 habilidades: 20 sin IA, 6 con IA opcional, 0 que dependan del modelo**, y
  un test congela que ninguna DETECCIÓN use el modelo. Además se arregló un bug
  del mismo día: el agente podía dejarle un aviso a un trader y el trader no lo
  veía nunca (el endpoint vivía bajo `/api/ia`, gateado por un módulo que un
  trader no tiene). Los avisos se mudan a `/api/avisos` sin gate de módulo,
  filtrando por el email propio, y aparecen como **PARA VOS** en la barra.

- **2026-08-19 — VALIDACIONES entra al agente como LO QUE SABE EXPLICAR, y la tab
  IA de observabilidad se elimina** (§0.m y §0.n). Las ocho pantallas de
  Manager → VALIDACIONES nunca fueron debug: son las preguntas de un trader con
  nombre de programador. Cinco pasan a ser capacidades del agente (tab **SABE**),
  **envolviendo** las funciones que ya existen — no se reimplementa un solo
  cálculo, y hay un test que lo exige. El determinista produce los números, el
  modelo produce UNA frase, y si el número recalculado no coincide con el que
  muestra la app eso viaja aparte como `discrepancia`: **es el puente entre "me
  preguntaste" y "te aviso"**. Las dos que no eran preguntas sino problemas se
  van a donde no haga falta apretar un botón: «Títulos sin flujo» pasa a control
  diario. Y la tab IA se elimina entera: lo único que importaba de ahí —que el
  gasto no se dispare y que las llamadas no fallen— es ahora el chequeo
  `ia:gateway`, una señal que te busca en vez de un tablero que hay que abrir.

- **2026-08-19 — SE DA DE BAJA EL COPILOTO, y SALUD queda solo adentro del
  agente** (§0.k y §0.l). *«Sirvió como inicial pero no cumplió con la necesidad;
  se elimina dando paso a AV AGENT, que va a ser una versión superior»* (user).
  Fuera el botón CONSULTALE A LA IA de todas las vistas, el asistente de negocio,
  el guía, el vigía de /trading y la navegación asistida — backend, front y docs
  (`COPILOTO.md`, `TOOLS_IA.md` y `QUANTAI.md` se borran; lo que había que
  recordar quedó acá). Fuera también **la IA que corría por atrás sin lector**:
  `triage` (cada 10 minutos contra una tabla que nadie abría), `ia_calidad`,
  el resumen ejecutivo de controles y el diagnóstico con IA de SALUD. **De 11
  tareas de IA quedan 4.** Regla nueva: una tarea existe solo si alguien lee su
  salida. SALUD pierde su panel, su botón y su modal; el motor no se tocó y el
  agente pasa a ser la única puerta, con la interrupción movida adentro (sin
  perder la capacidad que le dio origen). De yapa: un job con dos crons ya no
  aparece dos veces, y el módulo RBAC `asistente` se elimina.

- **2026-08-19 — LO QUE EL AGENTE SABE HACER: PROPONER → OK → APLICAR →
  VERIFICAR** (§0.j). El agente deja de solo diagnosticar. Cuatro acciones de
  arranque (la CARTERA de un asset, el EMISOR de un FCI desde su hermano, el
  alta de una contraparte ya sugerida, y **avisarle a una persona**), un ciclo
  único para todas, y una arquitectura donde sumar la quinta es una clase con
  tres métodos y una línea. La regla determinista resuelve lo que puede; el
  modelo entra solo para lo que quedó afuera y **elige de una lista cerrada**.
  Aplicar **relee de la base en el mismo request**: si no confirma, la propuesta
  queda `fallida` y no `aplicada`. `_write_sql` se movió del router al service
  (`assets_sql.set_campos`) — una sola puerta de escritura, un solo criterio.
  Rechazar también se registra: sin eso el agente parecería tener 100% de
  acierto. `mercado.av_agent_propuestas` + `para` en `av_agent_avisos` + 4
  endpoints + la lente «Esto lo sé hacer» en el diagnóstico de SALUD + 31 tests.

- **2026-08-17 — SALUD deja de estar aislada: el agente LEE EL LOG y propone el
  comando.** El user, mirando el diagnóstico de `mercado_1816_series`: *«sigo
  viendo aislado del agente… es la oportunidad perfecta para meter todo en el
  agente, y que tenga acceso a los logs o detectar bien el error que dio y
  analizarlo, y poder dar sugerencia como volver a correr»*. Tenía dos razones.
  **(1)** La fusión estaba solo en el backend: el front nunca llamaba a
  `/av-agent/salud`, así que en pantalla seguían siendo dos mundos. Ahora `salud`
  es la **cuarta puerta** del modal y comparte el MISMO componente que un bono —
  de solo lectura, sin botón de aplicar. **(2)** El agente mandaba a *«revisar los
  logs del scheduler»* **teniéndolos a mano**: `salud.detalle()` ya devolvía los
  errores y las últimas 40 líneas del `JobRunLogger`. *Un diagnóstico que manda a
  buscar lo que ya tiene enfrente no es un diagnóstico: es una derivación.* Tres
  lentes nuevas: **el LOG** (los errores reales, a la vista), **la FIRMA** (cruza
  el texto del error contra las lecciones — un 429 ya no es «revisá los logs»,
  es *«esto es la cuota de 50 tokens/día, y así se resolvió»*) y el **ARREGLO con
  el comando exacto**, que además avisa del cupo de 1816 ANTES de que la
  re-corrida lo gaste al pedo. Más `JOBS`, el catálogo de **qué alimenta cada
  job y de qué depende**: el diagnóstico decía «no se puede precisar qué vista
  queda tocada» y era cierto —nadie lo había escrito nunca— y sin eso una alerta
  no se puede priorizar. 2 tests (117).
- **2026-08-17 — LA MEMORIA: nada se desperdicia.** El user encontró al agente
  contradiciéndose en OLC3O — la lente del precio decía «ninguna fuente local
  tiene un precio mayor que 0» y tres pasos más abajo la MISMA pantalla mostraba
  «precio 137.280 (snapshot)». La causa: a las lentes se les pasaba un dict con
  solo tea/paridad/duration, **sin la clave `precio`**. No era un bono mal
  cargado: era el agente, y eso destruye la confianza en todo lo demás que dice.
  Se arregló el paso del precio **y** se construyó lo que faltaba para que una
  lección así no se pierda nunca más: **TRAZAS** (cada diagnóstico se guarda
  entero — antes se calculaba y se tiraba), **CONTRADICCIONES** (el agente se
  audita solo; el bug pasó tests, lint y una lectura humana, y lo único que lo
  caza es comparar dos frases separadas por seis renglones) y **LECCIONES**
  (síntoma → causa raíz → qué se cambió → commit, mostradas DENTRO del
  diagnóstico porque sirven cuando alguien está por decidir, no en un doc). Las
  lentes ahora declaran **hechos en máquina** y no solo prosa: la primera versión
  del detector comparaba texto y no cazaba nada. Más **CASOS PARECIDOS** —
  exemplar learning sin modelos: los otros bonos con la misma causa y sus votos
  del eval set. Sembradas las 7 lecciones de esta sesión, con `detectado_por`
  para saber **quién** encuentra los errores (3 de 7: el user). 3 tests (115).
- **2026-08-17 — LA FUSIÓN: SALUD entra al agente y el programa de IA queda en UN
  doc.** Decisión del user: *«el agente ES el nuevo proyecto de IA; hay que
  unificar todo lo de IA. Lo anterior no funcionó.»* Este doc pasa a ser la
  autoridad del programa (§0: principios, mapa contra el estado del arte, roadmap
  por capas) y `QUANTAI.md` queda como ARCHIVO de la primera fase — que nació al
  revés, primero el modelo y después el problema. **SALUD adentro**: un chequeo y
  un hallazgo son el mismo objeto, así que `detectar_salud()` es un detector más
  (`tipo="salud"`, en su propio `try` para que la observabilidad no pueda tumbar
  la relevada de bonos) y `av_agent_salud.diagnosticar()` lo razona con **8
  lentes** — qué es · ¿corrió cuando debía? · ¿salió bien? · ¿dejó el dato fresco?
  · ¿ya pasó antes? · qué se rompe aguas abajo · **la lectura con IA** (la única
  que gasta tokens, y va última) · qué haría falta para arreglarlo. Los pasos y el
  veredicto se **importan** de la puerta de bonos: que las dos cosas se vean igual
  en el modal es lo que permite que una sola cabeza lea las dos. **No escribe nada
  del lado de SALUD** y `ACCION_POR_TIPO["salud"] = None` es explícito — ve y
  razona, todavía no arregla. Y nace el **EVAL SET** (`mercado.av_agent_evals`): un
  ✔/✖ humano por diagnóstico, que rechaza un ✖ sin motivo y distingue un
  porcentaje con respaldo de uno con tres votos. Es lo que convierte cada paso de
  autonomía en una decisión con número en vez de fe. 4 tests (112 en el módulo).
  **Nada de lo que ya andaba cambió**: alta, cronograma, arreglo, detectores,
  vista, avisos y libro quedaron intactos.
- **2026-08-17 — LAS OCHO LENTES: el agente razona por varios lados.** Pedido del
  user: *«lo que yo quiero es el ANÁLISIS… es por el valor técnico, es por la
  paridad, es por la moneda, es porque falta esto»*. El diagnóstico era un árbol
  que **devolvía en el primer match**: acertaba la causa pero no mostraba el
  razonamiento, y el que lee la pantalla es el que decide si escribir. Ahora corren
  **todas** y cada una dice lo que ve — la ficha (¿qué es y quién le calcula la
  tasa?), la moneda (¿en qué unidad entra el precio al motor?), los insumos
  externos, el valor técnico (¿en qué escala está el cronograma?), el precio (de
  dónde sale y en qué escala), la paridad **con la división a la vista**, la TEA
  (¿converge el XIRR?) y el espejo en assets. La que falla **más aguas arriba** se
  lleva la causa (ese orden ya es el contrato), pero las otras siete igual hablan:
  una lente en verde también informa, porque **descarta un camino**. Todas locales:
  cero red, cero créditos. `diagnosticar_local` pasa a ser una vista angosta de
  `analizar()` y no una segunda implementación. 2 tests (108 en el módulo).
- **2026-08-17 — el token pegado a mano se IGNORABA por parecer vencido.** El user
  pegó un token de la sesión web —la vía de escape correcta cuando se agotan los 50
  logins— pero `expira_at` quedó NULL y `_token()` lo daba por vencido: la vía de
  escape no funcionaba y fallaba en silencio, con el mismo «auth 429» de siempre.
  El token sabe cuándo vence: `exp_del_jwt` lee su claim `exp` (sin validar la
  firma — no es nuestra) y la columna se sella sola. **Verificado en producción**:
  28 curvas traídas con 0 logins. Además, el 401 dejaba al cliente en LOOP
  (limpiaba el token de memoria y el siguiente `_token()` lo re-adoptaba de la
  fila); ahora también se invalida en la base, y solo si sigue siendo el mismo.
- **2026-08-17 — el «auth HTTP 429» era la CUOTA DE TOKENS, no un rate limit.**
  El panel del plan lo dice: créditos diarios **3.863/100.000** (sobra), máx. **1
  petición/segundo**, y **máx. 50 tokens por día** — ese era el techo. Un token
  dura **24 h**, así que UNO alcanzaría para todo el día, pero vivía en un dict de
  módulo (memoria de CADA proceso): `tamar_1816` corre 15 veces por día,
  `api.service` pide uno nuevo **en cada restart** —o sea en cada deploy—, más
  cada job y cada corrida manual. **Y el backoff lo empeoraba**: un `_auth` que
  falla reintentaba 5 veces, y *reintentar contra una cuota consume justo el
  recurso que se acabó*. Ahí está la lección general: **el backoff es la respuesta
  correcta a un rate limit (transitorio) y la peor posible a una cuota diaria.**
  Ahora el token es COMPARTIDO (`manager.tokens_externos`): se pide una vez y los
  demás procesos lo adoptan → de ~16-30 logins/día a **1-2**. `_auth` baja a 2
  reintentos, hay tope propio en 45 (margen bajo los 50) que **frena antes de
  gastar**, el error nombra la sospecha correcta en vez de mandar a «reintentá en
  un par de minutos», y el límite de **1 petición/segundo pasa a ser GLOBAL**
  (antes el throttle coordinaba dentro de un proceso y la API y los jobs son
  procesos distintos que no se ven). Presupuesto visible con
  `python -m scripts.diag_1816_tokens`. 2 tests.
- **2026-08-17 — el 429 de 1816 ya no frena la relevada entera.** `relevar()`
  arrancaba con `censar()` (~29 llamadas) **antes del primer detector**, y de los
  cuatro el único que necesita el universo de 1816 es `detectar_faltantes`. Mismo
  error de diseño que la cadena del arreglo, en otro archivo: una dependencia
  externa colgando de algo que casi no la necesita. Ahora cae a
  `research.mkt_1816_instrumentos` (la copia local del catálogo) y, si tampoco
  está, corre igual con los tres detectores que no dependen de la red. La
  degradación es HONESTA: **sin universo no se buscan faltantes** (reportar cero
  sería afirmar que no falta nada cuando no se pudo mirar) y la corrida declara de
  dónde salió el universo y con qué foto se comparó. 1 test.
- **2026-08-17 — E3.h, el diagnóstico deja de depender de 1816.** El agente tenía
  UN solo arreglo (traer el cuadro de 1816 y pisar los flujos), así que las cinco
  reglas de tasa pasaban por la misma cadena y sus dos puertas de 1816 la
  bloqueaban entera: con rate limit no decía **nada**. Medido antes de codear
  (`scripts/diag_av_agent_arreglos`, 38 hallazgos reales): **38 de 38 se resuelven
  con datos que ya están en la base**. La causa #1 son **dos vocabularios para el
  mismo hecho** — `rama_calculo` se migró a los ejes pero la rama ON sigue
  despachando por `moneda_flujo`, y cuando divergen el motor calcula con uno y la
  vista clasifica con el otro, sin error. Cuatro bonos tenían **`HD`** (el
  vocabulario de la CARTERA), que el `else` del motor convierte en ARS **en
  silencio**. Censo: **30 de 140 bonos** de la rama ON se contradicen y solo 8
  habían disparado hallazgo. Regla nueva `moneda_flujo_contradice` que mira el
  DEFECTO y no el síntoma (no necesita precio ni red), arreglo local con
  **verificación local** (la métrica tiene que volver al rango o no se escribe),
  precio con fallback a `snapshots_cierre` y a la evidencia congelada, y
  `paridad_del_motor` como la causa que el agente ve pero **no toca** (es un bug
  de `curvas.py:457`, no un dato). El alta y el cronograma quedaron intactos.
  6 tests (111 en total).
- **2026-08-17 — E3.g, la paridad dejó de ser el juez.** Con E3.f, DICP dio TEA
  **0 bps** y duration **0,00%** contra 1816 — y la cadena igual BLOQUEABA por la
  paridad (86,57% contra 90,19%), diciendo «otro cronograma». **Aritméticamente
  imposible**: dos cuadros distintos no dan la misma tasa al mismo precio y encima
  la misma duration al cuarto decimal. Error de JERARQUÍA: la paridad es la única
  de las tres que depende de una DEFINICIÓN (qué precio va arriba) además de los
  datos, así que no puede vetar a dos controles que dependen solo de los flujos.
  Medido, lo que difería es el numerador — 1816 usa su precio CLEAN y nosotros el
  que OPERA (su `precioDirty`, que para DICP **es** nuestro 48.600): llevando la
  nuestra a su clean da 90,11% contra 90,19% (0,08%), y en PARP 63,09% contra
  63,34% (0,25%). Su valor técnico y el nuestro **son el mismo número**. La cadena
  ahora muestra esa cuenta en vez de interpretarla. La prueba exige las DOS patas
  (TEA + duration) y hay contraprueba en el test: con la duration desalineada
  vuelve a bloquear. **Regla: un chequeo que depende de una convención no puede
  vetar a uno que depende solo de los datos.** 1 test (94 en total).
- **2026-08-17 — E3.f, el divisor del cuadro CER.** DICP daba TEA 3,91% contra
  9,25% y PARP daba EXACTO; la única diferencia estructural es que **PARP no
  amortizó nada todavía**. Medido: 1816 manda cada flujo **en pesos ajustados por
  el CER de su propia fecha**, así que la Σ del cuadro completo suma pesos de 2024
  con pesos de 2026 — y era nuestro divisor. Encima DICP capitalizó, con lo cual su
  total a amortizar es 126,99 del VN original y forzarlo a 100 es un segundo error.
  El divisor correcto es **`CER_liq / cer_emision`**: reproduce la TEA de 1816 con
  21 bps y la duration con 0,24% (contra 530 bps), y da los 5,000126% de prospecto
  en cada cuota de PARP. Invariante: **`monto_flujo_cer(f) × ratio` = el importe en
  pesos que publica 1816**. Consecuencias: el CER de emisión pasa a **BLOQUEANTE**
  (sin él no hay cuadro, no es algo a completar después), el CER se resuelve ANTES
  de convertir, y hay un paso nuevo **DIVISOR** con la Σ en % del VN original —
  auditable: 100 para un bono común, 118,30 para DICP. **La paridad del motor NO se
  tocó**: medido, 1816 la calcula contra el VN original igual que nosotros (0,08%
  de diferencia). **Riesgo gemelo declarado y NO medido**: los dólar-linked tienen
  la misma estructura con A3500, y los que probamos no habían amortizado — el caso
  en que los dos divisores coinciden y el problema no se ve. 4 tests (93 en total),
  uno de ellos congela que **para un bono sin amortizar el cambio es la identidad**,
  que es la garantía de que PARP y los 24 CER intactos del master no se movieron.
- **2026-08-17 — E3.e, el 500 de PARP y la pregunta abierta de DICP.** **(1) HTTP
  500 al aplicar**: `aplicar_flujos` llamaba a `acc.registrar(..., tabla=…)` y el
  libro de acciones **no tiene ese parámetro** — la tabla la resuelve él solo por
  la ACCIÓN (`DESTINOS`). `TypeError` **después** de que el UPDATE ya commiteó: el
  cronograma quedó escrito y el user vio un 500 pelado, sin llegar a QUÉ HIZO. Lo
  peligroso es DÓNDE cae: el libro está diseñado para no romper nunca la acción y
  lo cumple para cualquier fallo de la BASE, pero un `TypeError` pasa ANTES de
  entrar a su `try`. De paso, la acción se anotaba con destino «?» porque
  `DESTINOS` decía `completar_flujo` en singular. Test nuevo (AST): **toda llamada
  a `registrar` tiene que entrar en su firma y toda `accion=` tiene que ser una
  clave de `DESTINOS`**. **(2) DICP**: PARP cerró perfecto (TEA 0 bps, duration
  idéntica) y DICP no (TEA 3,92% vs 9,25%, paridad 86,57% vs 90,19%, duration
  3,4756 vs 3,2512). La única diferencia estructural es que **DICP ya amortizó y
  PARP todavía no**, lo que apunta a la BASE del cronograma: en base ORIGINAL los
  % son sobre el VN de emisión (Σ total = 100) y la paridad divide por el residual
  vivo; en base RESIDUAL los % son sobre lo vivo (Σ futura = 100) y divide por
  100. `engines/curvas.py` mezcla las dos —toma los flujos como vengan y calcula
  la paridad SIEMPRE contra `valor_nominal × ratio`—, y para un bono sin amortizar
  las dos coinciden, que es exactamente por qué PARP no lo mostró. **Cuál es la
  correcta no se decide leyendo código**: `scripts/diag_cer_amortizado.py` mide en
  qué base están escritos los CER que la mesa ya cargó (gratis) y qué residual
  aplica 1816 (con créditos). **Sin ese número no se toca el motor** — REGLA #2.
- **2026-08-17 — E3.d, DICP/PARP: el simulador leía un bono distinto del que
  valúa el motor.** La primera simulación real de un `flujos_vacios` volvió con
  el cuadro perfecto y **sin TEA**: duration 7,3781 (DICP) y 12,3808 (PARP). Medido
  antes de teorizar: son `dias_a_vto/365` — el valor del **bail-out** de la rama
  CER cuando falta `cer_emision` (`return` sin error ni log). Causa raíz de
  LECTURA: **un doc de `mercado.curvas` no es su blob `data`** — los ejes viven en
  COLUMNAS y `core/curvas_sql` los mezcla encima (`_COLS_FUERA_DEL_BLOB`); mi
  `SELECT data` dejaba `ajuste=None`, con lo cual `sin_cer` daba False y el
  encabezado acusaba a la escala del cuadro (que estaba impecable) en vez del CER
  faltante. Fix: `_doc_de_curvas` lee por `curvas_sql.cargar_todos()`, **la misma
  fuente que el motor**. Además: (1) el CER de emisión se **pide en la propia
  cadena** (paso `pide`, como E2.x) y se escribe con él —única excepción a «acá
  solo se escribe el cronograma», detrás del guard `cer_manual`—; (2) los cupones
  **ya pagados** se cuentan aparte y la cadena explica que el cuadro se guarda
  COMPLETO y el motor valúa solo los futuros (verificado: las 4 ramas filtran
  `fecha_flujo(f) > fecha_settlement` — el motor ya lo hacía, faltaba **decirlo**),
  y sin ningún cupón futuro el paso **BLOQUEA**; (3) el front tenía la cadena
  escrita DOS veces (`AccionAlta`/`AccionFlujos`) y por eso el input del dato
  faltante existía solo en el alta — ahora es **un** `AccionCadena` parametrizado
  por `modo`. **Regla: si el simulador y el motor no leen por la misma función,
  tarde o temprano ven bonos distintos.** 2 tests (89 en total).
- **2026-08-17 — E3.c, el botón que no aparecía y no avisaba.** El IGNORAR
  funcionó de una; el de completar el cronograma no se renderizó **sin dar
  error**: el front comparaba `h.tipo === "flujos_vacios"` y `flujos_vacios` es la
  **REGLA**, no el **TIPO** (`sin_flujo`). Mismo par que RAMA/CURVA en E2.o — dos
  vocabularios que se parecen, conviven en la misma estructura y confundirlos no
  rompe nada, solo deja de hacer algo. Fix: el front **no tiene strings de tipos**
  — la acción la decide `ACCION_POR_TIPO` en el backend y viaja en el hallazgo
  (`h.accion`), **derivada en la lectura** para que un tipo que se vuelva
  accionable alcance también a los hallazgos ya guardados. Test que congela lo que
  el front no puede verificar: las claves del mapa tienen que ser TIPOS que algún
  detector emita. **Regla: un string que el front escribe a mano y el backend
  también conoce es el bug esperando.** 2 tests (87 en total).
- **2026-08-17 — E3.a + E3.b, la lista se limpia y el segundo hallazgo se
  acciona.** **(a) IGNORAR en toda fila.** El mecanismo existía pero solo se
  disparaba contestando una pregunta del agente, y el filtro llegaba **solo a
  `detectar_faltantes`** — los otros tres detectores seguían mostrando lo ya
  descartado. Ahora `tickers_ignorados()` es el único lector y el filtro se aplica
  **una vez sobre la lista completa**, tanto al relevar como **al leer** (sin esto
  el botón no surtía efecto hasta la próxima corrida de ~29 créditos: la regla de
  E2.r). Por TICKER, reversible desde DECIDIDO. **(b) `flujos_vacios` accionable**:
  `simular_flujos` / `aplicar_flujos` + endpoints. Es el alta al revés — el bono ya
  existe y **sus ejes los cargó la mesa**, así que `rama_calculo` sale del doc y no
  se re-derivan. Mismo cotejo contra 1816 (paridad + duration + cota del
  devengado), que es lo que el user pidió chequear. **El UPDATE mergea el blob con
  un parche que solo trae el cronograma**: emisor, curva, símbolo, ejes y
  `cer_emision` no están en el payload, así que no se pueden pisar. Un paso de la
  cadena enumera qué se escribe. 2 tests (85 en total).
- **2026-08-17 — E2.z, el CER estaba y el mensaje acusaba a la serie.** El agente
  decía *«la serie CER no llega hasta 2025-11-28»* con el dato presente en la
  base. **(a)** El mensaje interpolaba la fecha de EMISIÓN etiquetada como
  «(T−10 hábiles)» → nombraba un día que nunca se buscó; el T−10 real es
  **2025-11-12** (16 días corridos: 2 findes + 2 feriados) y ahí el CER vale
  651,898. **(b)** `get_cer_liquidacion` resuelve el T−10 indexando
  `mercado.dias_habiles` y devuelve `None` si la tabla no llega tan atrás; el
  llamador leía ese `None` como «no hay CER», una conclusión que la función nunca
  afirmó — tres causas distintas colapsadas en una frase que culpaba siempre a la
  serie. Fix: eslabones separados, el mensaje nombra **las dos fechas**, y la
  fecha sale del calendario oficial o —si no alcanza— de `calendario.restar_habiles`
  (puro, `holidays.Argentina`), diciendo por cuál vía. **Reglas: un mensaje de
  error nombra el insumo que la función realmente usó; y un `None` con varias
  causas no autoriza a escribir una de ellas como hecho.** 1 test (83 en total).
- **2026-08-17 — E2.y, el chequeo de SALUD que no podía detectar nada.** El
  contrato de `macro.series_macro` hacía `MAX(fecha)` **sin filtrar por `serie`**
  sobre una tabla con DOLAR, CER, BADLAR, TAMAR, RiesgoPais e Inflación juntas:
  **respondía por la serie más fresca**, así que con el dólar al día el CER podía
  estar congelado hace meses y el tablero verde. Sexta aparición del mismo
  anti-patrón —**un agregado que tapa el detalle**— y la peor, porque en un
  chequeo de salud no solo esconde el problema: certifica que no lo hay. Fix: los
  contratos aceptan `filtro` y la tabla se declara **una vez por serie crítica**,
  cada una con su tolerancia y su id (silenciable por separado). Explícitas y no
  agrupadas: agrupar pondría en rojo cada serie mensual o discontinuada. Además el
  agente distingue ahora **serie ATRASADA** de **HUECO** —dos problemas distintos
  que su mensaje confundía— y va `scripts/diag_cer_serie.py` (read-only) para
  medir rango, huecos por tramos y la ventana real de `cargar_cer`. 1 test.
- **2026-08-17 — E2.x, el dato se pide en la CADENA, antes de aplicar.** E2.w lo
  pedía DESPUÉS del alta (en AVISOS); el user pidió que se pida antes y se
  re-simule con él — que es la diferencia entre aplicar a ciegas y aplicar viendo
  la tasa. Un paso ahora puede declarar **`pide`** (campo/label/tipo/ayuda) y la
  cadena renderiza su input; lo tipeado viaja **igual a SIMULAR y a APLICAR**, así
  que se aplica exactamente lo que se vio simulado. `simular()` y `aplicar()`
  aceptan `cer_emision`: el bono **nace con el dato** y por lo tanto **no genera
  aviso**. El valor manual **gana** sobre el derivado (lo sacó del prospecto o del
  BCRA, una fuente que el sistema no tiene) y uno inválido se ignora sin romper la
  simulación. El estado del input vive por hallazgo — compartirlo filtraría el CER
  de un bono al siguiente. 1 test (82 en total).
- **2026-08-17 — E2.w, el aviso se completa desde la lista.** El aviso del CER de
  emisión te mandaba a Manager → Títulos: el agente hacía el 95% y el 5% quedaba
  a tres clics. Ahora cada aviso viaja con su `campo` (label/tipo/ayuda) y la
  fila trae input + GUARDAR → `POST /api/ia/av-agent/aviso/completar` escribe y
  cierra en el mismo acto. **Solo campos del catálogo** `_CAMPO_AVISO`, y
  **verifica antes de cerrar** releyendo con el mismo predicado de `_COND_AVISO`:
  si el dato no quedó, el aviso sigue abierto. Frontend: `FilaAviso` con estado
  propio por fila (un `valor` compartido haría que escribir en uno pisara otro).
- **2026-08-17 — E2.v, la diferencia esperada + la duration como segundo
  testigo.** BPOA8 quedaba en «revisar» con la TEA y la duration clavadas y sin
  ningún dato que cargar. **(a)** La diferencia de paridad contra 1816 **es el
  interés corrido** —la nuestra es sobre el residual y la de ellos sobre el valor
  técnico— así que da siempre más alta, con techo de **un cupón entero**:
  `cota_devengado()` lo deriva del cuadro. Cota y no predicción, para no tener
  que adivinar la convención de días de 1816; y no pierde detección porque un
  error de escala mueve la paridad 100× o 1.000×. **(b)** La **duration** pasa a
  votar: la paridad detecta la ESCALA y es ciega a las fechas, la duration al
  revés — una duration que no coincide ahora BLOQUEA aunque la paridad esté
  perfecta. **(c)** El veredicto solo dice «a mano» si hay un dato que cargar
  (un `revisar` con aviso); si es juicio lo dice así. 3 tests (81 en total).
- **2026-08-17 — E2.u, GD46 cerrado con el diag en la mano.** Las dos hipótesis
  de E2.t eran falsas. **(a)** 1816 NO renormaliza: manda el cronograma completo
  desde la emisión (GD46 arranca en 2021-07, Σ=100), así que el residual vivo se
  deriva del cuadro — 44 amortizaciones de 2,272739 desde el cupón 8, 4 pagadas →
  **residual 90,909**, contra los 91,3153 de valor técnico de 1816 (la diferencia
  son 0,41 de interés corrido). La rama `soberanos` de `convertir_flujos` ahora
  escribe `residual_previo_pct`, que el motor usaba defaulteado a 100. **(b)** El
  sondeo de 14 nombres de campo dio 14 rechazos: no había ningún campo de residual
  que pedir. **(c) Hallazgo nuevo**: la paridad de 1816 **cambia según la moneda
  del pedido y no es una reexpresión** — GD46 da 0,7556 en `mep` y 0,7278 en `ars`
  (precio a TC 1.514,5, valor técnico a 1.572,4). Nace `moneda_cotejo_1816`: el
  precio se PIDE en la moneda que espera el motor y el resultado se COMPARA en la
  moneda en que el motor lo calcula, pasándole a 1816 el mismo número convertido
  por `precio_soberano_a_usd`. **Paridad nuestra 75,74% vs 75,56% → 0,24%.**
  **(d)** el cuadro de auditoría muestra la MISMA paridad que usa el cotejo.
  3 tests (78 en total).
- **2026-08-17 — E2.t, el A3500 del dólar-linked + el residual del soberano.**
  Dos bonos con el mismo mensaje de error y dos causas distintas; en ninguna el
  cuadro estaba mal. **(a) D30O6**: el simulador nunca pasaba `tc_a3500`, así que
  todo dólar-linked salía por la puerta de emergencia de la rama con la duration
  ingenua (0,2027 = 74/365, los días al vencimiento). Causa raíz: **dos criterios
  para una pregunta** otra vez — el motor decide el TC por RAMA y el simulador
  preguntaba por `moneda_flujo`. Ahora usa `curva_depende_de`, el predicado del
  motor. La nota del encabezado también deja de culpar a la escala cuando lo que
  falta es un TC. **(b) GD46**: la paridad de un soberano se calcula contra
  `residual_previo_pct` y nuestro conversor no lo escribe → el motor usa 100 y la
  paridad sale igual al precio en dólares (68,86% contra 72,78%). **No se fixea a
  ciegas**: la Σ del cuadro de 1816 es 100,000012, o sea que parece venir ya
  renormalizado y el residual no estaría ahí — derivarlo de la Σ daría 100 de
  nuevo. Entregable: `scripts/diag_av_agent_flujos.py` (read-only) que dumpea el
  cashflow crudo y **sondea de a un nombre por vez** qué campo de residual acepta
  la API. **(c)** el cuadro «CÓMO SE CALCULÓ» muestra las dos paridades en la
  misma unidad. 2 tests (76 en total).
- **2026-08-17 — E2.s, GD46: doble conversión por MEP.** La paridad daba 0,0455
  contra 0,7556 de 1816 y parecía un problema del cuadro de flujos. **El flujo
  estaba perfecto**: `69 / 91,315 × 100 = 75,57 %` contra el 75,56 % de 1816. Lo
  mal era la UNIDAD del precio — yo pedía `moneda="mep"` (ya en USD) y
  `precio_soberano_a_usd` volvía a dividir por MEP porque el símbolo
  `MERV - XMEV - GD46 - 24hs` no tiene sufijo `D`/`C`. Bug propio de E2.g, y **no
  daba error**: sin TEA el motor devuelve la duration naive (19,9 ≈ años al
  vencimiento) y sigue. Fix: `moneda_pedido_1816()` **deriva** la moneda del pedido
  del mismo predicado que usa el motor, en vez de elegirla aparte. **Regla: la
  unidad de un dato la decide el motor que lo consume, no quien se lo pasa.**
  2 tests (74 en total).
- **2026-08-17 — E2.r, el filtro de Primary corre también AL LEER.**
  Los BPO seguían apareciendo porque el filtro vivía SOLO en el detector, que
  corre al relevar (~29 créditos, no se hace por pantalla) — así que no iba a
  surtir efecto hasta la próxima corrida. Tercera vez el mismo error en la misma
  función, contra una regla que yo mismo escribí en E2.m. Fix estructural:
  `descartar_por_primary()` es UN predicado —excepción de cartera incluida— que
  llaman el detector y la lectura, así que no puede aplicarse en un lado y
  olvidarse en el otro. **Regla que queda: todo criterio que decida si algo se
  muestra tiene que poder evaluarse en la LECTURA.** 1 test.
- **2026-08-17 — E2.q, el filtro de Primary + el dólar-linked.** (a) Lo
  que **no cotiza en Primary deja de reportarse**: no se puede valuar nunca, así
  que es ruido permanente (pedido del user). Se prueban las patas 24hs y CI
  antes de descartar, **lo que está en CARTERA se reporta igual** —ahí no valuar
  es más grave— y los descartados van al log con su cuenta. Sin universo de
  Primary no se filtra. (b) **`dolar_linked` entra a las ramas automáticas**: el
  motor dice textual que su shape es «igual que soberanos» y llama a la misma
  función, así que bloquearlo era una hipótesis mía que el código desmiente —y
  el pre-flight se contradecía con su propio paso 10. La conversión **normaliza
  por la Σ** porque estos vienen en nominales de la emisión (Σ=148.869,84 medido
  en D10Y7/D30O6) y pasarlos crudos daría una TEA absurda sin error. (c) Se borró
  el «paso 15» fantasma (un `<li>` suelto dentro del `<ol>`). 4 tests.
- **2026-08-17 — E2.p, el catálogo manda sobre la constante.** BADLAR
  seguía reportada como «ajuste sin curva» con la curva YA creada, y el alta de
  un BADLAR se rechazaba por esa misma curva. Dos síntomas, una causa: **dos
  fuentes para «¿qué curvas existen?»**. (a) El filtro de hallazgos caducados de
  E2.m aplicaba UN predicado a dos tipos cuyo campo `ticker` significa cosas
  distintas —en `hueco_de_curva` es el AJUSTE, no un bono—; ahora cada tipo
  caduca por su razón y la de éste es `ajuste_sin_curva`, la misma función que
  lo detecta. (b) `CURVAS_BONO` era una tupla a mano: pasa a ser el PISO y el
  catálogo la amplía (`curvas_validas()`), así que crear una curva la deja
  escribible en el mismo acto. 2 tests.
- **2026-08-17 — E2.o, RAMA ≠ CURVA.** El alta de un TAMAR moría al
  escribir («curva inválida: 'otros'»): `aplicar()` mandaba `curva = rama` y son
  dos vocabularios que **coinciden en 4 de 5 valores** —por eso sobrevivió—; un
  TAMAR cae en la rama `otros` pero su curva se llama `tamar`. Nuevo
  `curva_destino()`, que además devuelve `""` para `badlar`/`tpm`/`caucion` (no
  tienen curva) en vez de forzar una parecida. Y **paso nuevo del pre-flight —
  «La escritura va a ser aceptada»**: validaba 13 cosas sobre los datos y ninguna
  sobre si el write iba a entrar, contra la misma constante que usa el writer.
  2 tests.
- **2026-08-17 — E2.n, se elimina el SEGUNDO gate.** Tercera vez
  el mismo bug: cadena en verde, veredicto diciendo «se puede aplicar» y sin
  botón APLICAR, porque `aplicable` contestaba la misma pregunta con otro
  criterio (E2.k arregló media expresión; la otra mitad —el CER— siguió
  bloqueando cuando E2.l lo sacó de la cadena; y el front encima hacía
  `aplicable && puedeAplicar`, donde el AND vuelve gate al más restrictivo).
  **`aplicable` deja de gatear**: su contenido ya viaja en el paso `rama`, que
  BLOQUEA solo, así que el veredicto lo cubre — ahora solo pinta el motivo, se
  borró el early-return de `aplicar()` y el front lee UNA condición. La regla:
  dos gates para una decisión no se contradicen si alguien se equivoca, se
  contradicen SIEMPRE. 1 test que falla si vuelve a aparecer un segundo gate.
- **2026-08-17 — E2.m, cierre manual del aviso + la foto se coteja.** Los avisos
  se **PERSISTEN** (`mercado.av_agent_avisos`) y **los cierra el user**, no el
  sistema: derivarlos (mi diseño de E2.l) confundía «el dato está» con «yo ya me
  ocupé», y borraba la lista de tareas sola. El cierre manual se hace seguro con
  **`ya_cargado`** —cruce contra el master en la misma query— que canta un aviso
  marcado hecho sobre un dato ausente, y sugiere marcar uno abierto cuyo dato ya
  está. **Y los hallazgos caducados dejan de mostrarse**: TZXM8 seguía en
  «faltantes» después de que el agente lo creara, porque la lista es la foto de
  la última corrida (relevar cuesta ~29 créditos, no se rehace por pantalla) —
  ahora se contrasta contra `mercado.curvas` antes de mostrarla y caducan solo
  `falta_en_base`/`hueco_de_curva`. Regla que queda: **la foto se muestra, pero
  nunca sin cotejarla**. Endpoint nuevo → `MAPA_APP.md` regenerado (estaba stale
  desde antes: `/api/ia` decía 11 endpoints y tiene 17). 1 test.
- **2026-08-17 — E2.l, AVISOS y causalidad entre pasos.** El CER de emisión **ya
  no bloquea**: pasa a `revisar` + aviso, porque negarse a hacer el 95% del alta
  por un dato que ninguna fuente publica es tirar el trabajo hecho (decisión del
  user). Tab **AVISOS** nueva, **derivada en vivo** contra el master → se cierra
  sola al cargar el dato, sin botón de «resuelto». **Causalidad**: verificado en
  `engines/curvas.py:438` que sin `cer_emision` la rama CER sale con solo
  duration, así que UNA causa pintaba TRES pasos en rojo y el del precio mandaba
  a revisar la escala, que no tenía nada que ver — ahora el paso consecuente
  nombra la causa y no cuenta como hallazgo propio. Y **todos los mensajes
  reescritos a una línea**: el detalle de un paso no puede ser un párrafo. 2 tests.
- **2026-08-17 — E2.k, los CINCO estados y qué frena qué.** `atencion` mezclaba
  «esto está mal» con «esto es lo que va a pasar», así que **una contradicción
  probada del cronograma se leía igual que un aviso de rutina** y el alta quedaba
  habilitada (GD46: paridad 0,05% vs 75,56%, veredicto «se puede aplicar»). Nuevo
  modelo `ok / info / revisar / bloquea / no_se`, cada uno con UN significado, y
  **dos booleanos derivados**: `puede_aplicar` (humano) y `puede_auto` (la lane de
  E6 — `revisar` y `no_se` frenan al robot aunque no al humano). Tres bugs del
  mismo patrón —dos lugares contestando la misma pregunta— corregidos: el cotejo
  ahora **bloquea**, «hay precio pero el motor no da TEA» **bloquea** (salvo tasa
  externa, donde es lo esperado), y `aplicable` dejó de contradecir a la cadena
  (TMG27 mostraba todo verde y escondía APLICAR). El botón oculto se reemplazó
  por `✘ BLOQUEADO`. 7 tests nuevos.
- **2026-08-17 — E2.j, el retroceso de ruedas volvió a funcionar.** **Regresión
  propia de E2.g**: al pasar de 4 a 13 campos entró metadata que 1816 manda
  siempre (`fuente`, `convencionTna`, `fechaLiquidacion`), el predicado de «trajo
  datos» daba True en la primera vuelta y **el retroceso nunca corría** — un
  domingo, o un papel sin operar ese día, contestaba «no publicó precio» en vez
  de traer la última rueda buena. Fix en tres capas: `CAMPOS_METADATA` (la ficha
  del pedido no es dato), `campos_dato=` (el simulador frena solo con PRECIO, el
  job de TAMAR sigue frenando con la tasa) y el mensaje, que ahora dice qué
  ruedas se probaron y muestra `ultimaOperacion`. 3 tests de regresión.
- **2026-08-17 — E2.i, el alta entra COMPLETA.** Un TAMAR/BADLAR ahora **nace con
  su TASA y su MARGEN** (upsert compartido en `core/tamar_1816_sql`, sin esperar
  al cron; sin dato de 1816 no se escribe una fila en NULL). Y el bono entra con
  la FICHA: **emisor** (1816 es la fuente de verdad y estaba a un SELECT),
  `fecha_emision`, `tipo`, `tasa_referencia` y `cupon_anual` solo si es cero
  cupón. Paso nuevo del pre-flight que lista qué se escribe y qué queda vacío.
- **2026-08-17 — E2.h, la PARIDAD es el juez.** Medido: pedir `mep` bajó GD46 de
  202 a 139 bps, y el resto es convención (`180-360` vs nuestros días reales).
  Entonces el cotejo compara **paridad** —que depende solo del precio y del
  cronograma— y deja la TEA como apoyo, con el motivo de su diferencia. Se
  normaliza la escala (nuestro % vs su fracción), con test. Confirmado también
  que `precioDirty` ES su insumo de cálculo, y que `mep` sobre un bono en pesos
  da un precio absurdo.
- **2026-08-17 — E2.g, los 202 bps resueltos con el OpenAPI.** `moneda` es
  `ars|ccl|mep` y con el default **1816 divide por CCL mientras nosotros
  dividimos por MEP** — esa era la diferencia; los bonos USD se piden con `mep`.
  El precio pasa a **`precioDirty`** (el de mercado, verificado contra la paridad
  que ellos publican). Y el cotejo usa el endpoint de **INPUT MANUAL**: su tasa a
  NUESTRO precio, que elimina el insumo como variable. +8 campos en la memoria de
  cálculo, con `convencionTna` a la cabeza.
- **2026-08-17 — E2.f, CER cero cupón + relevamiento de 1816.** **Bug**: un CER
  de un solo pago se guardaba como bullet (`flujo_vencimiento`) y la rama `cer`
  no lo mira → sin tasa y sin error. El atajo queda solo para `tasa_fija`. La
  alarma de escala deja de sonar en `cer` (la conversión normaliza). El error de
  1816 viaja completo, y si la moneda del bono no se acepta se cae a `ars` en vez
  de quedarse sin precio. Nuevo `scripts/diag_1816_indicadores` — read-only,
  prueba de a uno qué monedas y qué campos acepta la API de verdad.
- **2026-08-17 — E2.e, siembra + TAMAR + memoria de cálculo.** El alta **siembra
  la especie** como paso final (patas ARS y USD, lógica compartida en
  `core/especies.py`) y deja de exigirla como requisito. El agente aprende que a
  un **TAMAR no le calculamos la tasa a propósito** (`TASA_EXTERNA_EN_CODIGO` +
  `fuente_valuacion` unificada; `jobs/tamar_1816` lee `ajustes_de_1816()`), así
  que deja de reportarlo como falla y el cotejo no aplica. Banda del cotejo
  **300 → 150 bps**. Bloque **CÓMO SE CALCULÓ** con cada insumo y su fuente, y el
  precio de 1816 se pide en la **moneda del bono** (hipótesis de los 202 bps).
- **2026-08-17 — E2.d, precio de referencia + CONTROL CRUZADO.** Sin snapshot se
  usa el `precioClean` de 1816 (no se persiste) para poder simular un bono nuevo,
  y se compara **nuestra TEA contra la de ellos** sobre el mismo precio — la
  única evidencia de que el cuadro está bien convertido. La banda sale de los 7
  bps medidos en el TAMAR y una contradicción avisa sin bloquear. La lógica de
  fecha/retroceso de `indicadores` se movió a `core.mercado_1816`.
- **2026-08-17 — E2.c, el PRE-FLIGHT.** El simulador devuelve la **cadena
  completa** (8 pasos, con los verdes incluidos) + veredicto, y un paso en falla
  BLOQUEA el alta. El símbolo sale de `mercado.especies` en vez de armarse a
  mano; `aplicar` dejó de pagarle a 1816 el mismo cuadro dos veces; el libro
  guarda qué NO estaba en verde al aplicar.
- **2026-08-17 — E2.b, calibración.** Motivo por RAMA (el texto fijo hablaba de
  CER hasta en un BADLAR); **CER pasa a alta automática** con `cer_emision`
  inferido de la fecha de emisión de 1816 + nuestra serie CER, y su cupón
  convertido a TASA sobre el residual vivo; y el simulador chequea si **Primary
  lista el símbolo** — sin eso el bono nunca recibe precio.
- **2026-08-17 — E2, simular y aplicar.** El agente baja el cuadro de 1816,
  calcula la TEA en seco y da de alta el bono por `upsert_bono` con un click.
  Solo las ramas donde la conversión es inequívoca (`tasa_fija`, `soberanos`);
  `cer` y `tamar` se simulan pero las carga un humano.
- **2026-08-17 — E1.i, el LIBRO DE ACCIONES.** `mercado.av_agent_acciones` + tab
  **HIZO**: qué escribió, cuándo, en qué tabla, por pedido de quién y qué había
  antes. Anota también los intentos fallidos. Obligatorio antes de E2.
- **2026-08-17 — E1.h, el agente CREA la curva.** `mercado.curvas_catalogo` +
  `core/curvas_catalogo.py`: la curva deja de ser código y pasa a ser dato. El
  hueco detectado se convierte en una pregunta (`1816` | `motor` | `despues`) y
  responderla la crea. El catálogo SUMA y nunca pisa; si la tabla no responde,
  todo anda como antes. Se borró `diag_curva_nueva` (medía algo que ya estaba
  probado por los TAMAR).
- **2026-08-16 — E1.g, tres bugs de la corrida real.** El job contaba un tipo de
  hallazgo que no imprimía (total 59, bloques 58); el diag de curva nueva
  concluía sobre n=1 y ahora suma el universo de 1816 (0 créditos) y avisa si la
  muestra es chica; y el tab de preguntas vacío decía "no pasa nada" al lado de 38
  hallazgos altos.
- **2026-08-16 — E1.f, huecos del SISTEMA.** Detector `hueco_de_curva`: los
  ajustes que existen en `mercado.curvas` pero no tienen pill (`badlar`, `tpm`,
  `caucion`) dejan bonos INVISIBLES sin dar error. Se reporta por ajuste, va
  primero, y la pregunta de alta lo avisa antes de que alguien cargue diez bonos
  que no va a poder mirar. Derivado de `_pill_de_ajuste`, así se apaga solo.
- **2026-08-16 — E1.e, contexto en las preguntas.** Emisor, denominación, moneda
  y vencimiento (del mismo crédito del censo) + el aviso **«lo tenés en cartera y
  no valúa»**, que sube esos faltantes a severidad alta y los pone primeros.
  `registrar()` ahora refresca el enunciado de las preguntas ABIERTAS (nunca el de
  las respondidas). 8 tests nuevos.
- **2026-08-16 — E1.d.b, de vista a MODAL.** El link del nav y la página
  `/av-agent` se borraron: el agente vive en un botón de la barra inferior (junto
  a BRIEFING y SALUD) que abre un modal, con contador de preguntas visible desde
  cualquier pantalla. Nadie navega a un agente. **Todo pasa a admin-only** (antes
  la lectura era módulo `ia`): expone el estado interno de la valuación, y con la
  mesa teniendo `ia` para los copilotos eso se lo mostraba a un comercial.
- **2026-08-16 — E1.d, la VISTA.** `GET /api/ia/av-agent/vista` (todo en un
  request) + responder + designorar, y la pantalla `/av-agent` en el front con el
  link en el header. Las preguntas son el tab default; el agente habla en primera
  persona y dice lo que todavía no puede hacer. Bug atajado antes de prod: el
  deshacer nació DELETE y el proxy de `/api/ia` solo expone GET/POST.
- **2026-08-16 — E1.c, el agente PREGUNTA.** `mercado.av_agent_preguntas` +
  `api/services/av_agent_preguntas.py` + `--preguntas`/`--responder` en el job.
  Responder dispara un efecto (`ignorar` → `av_agent_ignorados`), no repregunta
  (clave única), acepta rangos, y las 3 decisiones abiertas del §4 pasan a ser
  preguntas del propio agente. 14 tests nuevos.
- **2026-08-16 — RENOMBRE a AV AGENT** (decisión del user). Era `curador`; se
  cambió antes de que la tabla persistiera una sola fila, así que la vieja se
  dropea en vez de migrarse.
- **2026-08-16 — E1.b, primera calibración contra prod.** 107 hallazgos, 29
  créditos. Tres falsos positivos corregidos (LECAP zero-coupon, ONs fuera de
  cartera, patas `@` de 1816) + los Globales en EUR excluidos por regla de moneda
  + `mercado.av_agent_ignorados`. Los tres tenían la MISMA causa: un predicado
  que el sistema ya tenía y que se reescribió peor. El detector de tasas acertó
  (12 bonos con la falla #4) y `sin_ejes` dio 9/9 exactos contra `RENTA_FIJA` §14.
  6 tests nuevos congelan cada falso positivo.
- **2026-08-16 — E1 codeado.** `core/mercado_1816.censar()` (promovido del diag),
  `api/services/av_agent.py` (3 detectores puros), `jobs/av_agent.py`,
  `mercado.av_agent_hallazgos` y 15 tests. `curvas_vista._es_ruido` pasó a pública
  (`es_tasa_ruido`) para que el criterio de "tasa ruidosa" exista UNA sola vez.
  Read-only sobre `mercado.curvas` y sin una línea de IA. **Pendiente: calibrar en
  prod** — hasta entonces, sin cron.
- **2026-08-16 — E0.** Nace el doc. Se define el encuadre (agente de integridad de
  datos, no copiloto), dónde va y dónde no va la IA, las 7 etapas, las 3 decisiones
  abiertas (alcance, política de conflicto, alcance del diagnóstico) y el inventario
  de lo reusable verificado contra el repo. Absorbe el diseño de
  `VISTA_RESEARCH.md` §4.10, que queda como el registro de la medición.
