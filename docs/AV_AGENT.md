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
| 1 | **Medición** — eval set con voto humano | saber si acierta | 🟡 tabla y endpoints, sin UI |
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

### 0.f El eval set (2026-08-17)

`mercado.av_agent_evals` — un ✔/✖ humano por diagnóstico, con la causa correcta
cuando falla. **El dataset ya existía y se estaba tirando**: cada vez que alguien
abre un diagnóstico y decide, emite un juicio sobre si la causa era la correcta.

- **Un ✖ sin motivo se rechaza**: de «está mal» no se aprende nada.
- `MIN_VOTOS` separa un porcentaje con respaldo de uno con tres votos — 2 de 2 no
  es «100% de acierto», es «casi no hay evidencia».
- `candidata_a_auto` **no es un permiso**: es lo que el número habilita a
  discutir. La lane automática se prende a mano, siempre.

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
