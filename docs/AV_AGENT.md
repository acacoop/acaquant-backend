# AV AGENT — el diario ⟨HISTÓRICO, PODADO⟩

> # ⚠️ DOC HISTÓRICO — NO ES LA ESPECIFICACIÓN
>
> El agente se rehizo entero el **2026-08-24**. La especificación viva es
> **`docs/AGENT_2.0.md`**; **nada de acá describe el código actual**.
>
> Se conserva porque cada §0.x es **un bug real y la decisión que lo cerró**.
> Sirve para no repetirlos, no para saber cómo funciona el agente hoy.

## ⚠️ PODADO EL 2026-08-31 — qué quedó y por qué

Este diario tenía **120 secciones y 611 kB**, y era el archivo más grande del
repo: el 36% de todo `docs/`. Medido contra el código: citaba **39 archivos, 27
tablas y 17 rutas HTTP que ya no existen** — casi todo el agente viejo (los 34
services `av_agent_*`, `core/ciclo.py`, el eval set, los votos, las siete tabs).

Borrarlo entero no se podía: **25 secciones están apuntadas por número desde
el código** («Doc madre: `docs/AV_AGENT.md` §0.x» en `agente/`, `core/`,
`engines/`, `api/`, la CI y el `CLAUDE.md` raíz). Un ancla rota manda al lector a
buscar algo que no está.

**El criterio, entonces:** se conservan las secciones que algo VIVO cita, más las
que ésas citan a su vez (39 en total). Se podaron **81** que no cita
nadie. El original completo está en git (`git show HEAD~1:docs/AV_AGENT.md`).

> ⚠️ Adentro del texto conservado puede quedar un `§0.zz` que apunte a una
> sección podada. Es eso: podada, no perdida.

**Secciones podadas:** §0.a · §0.ae · §0.ag · §0.ah · §0.aj · §0.ak · §0.ao · §0.as · §0.at · §0.au · §0.av · §0.aw · §0.ax · §0.az · §0.b · §0.ba · §0.bb · §0.bc · §0.be · §0.bf · §0.bg · §0.bh · §0.bi · §0.bj · §0.bk · §0.bl · §0.bm · §0.bn · §0.bo · §0.bp · §0.bq · §0.bs · §0.bt · §0.bv · §0.bw · §0.bx · §0.by · §0.bz · §0.c · §0.ca · §0.cb · §0.cc · §0.cd · §0.cf · §0.cg · §0.ch · §0.cj · §0.ck · §0.cl · §0.cm · §0.cn · §0.co · §0.cp · §0.cs · §0.ct · §0.cu · §0.cy · §0.cz · §0.d · §0.da · §0.db · §0.dc · §0.dd · §0.de · §0.df · §0.dg · §0.dh · §0.di · §0.dj · §0.dk · §0.dl · §0.dm · §0.dn · §0.do · §0.e · §0.g · §0.h · §0.i · §0.n · §0.w · §0.z

---
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
  la lista de pendientes del agente (`agente.av_agent_avisos`): tiene alta,
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

- Tablas: `agente.av_agent_propuestas` (UNIQUE `accion+sujeto+campo` → re-proponer
  ACTUALIZA en vez de acumular diez para el mismo asset) y la columna `para` de
  `agente.av_agent_avisos`.
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

---

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

---

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

---

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

---

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

---

### 0.p EL AGENTE ES ADMIN-ONLY, PERO LO QUE MANDA LE LLEGA A CUALQUIERA (2026-08-19)

> *«El AV AGENT es SOLO para admin, no para el resto. Aunque esto no quiere decir
> que no tenga el poder para mandar una alerta, notificación, etc. a otro user
> que no sea admin.»* (user)

**Y ahí había un bug real, introducido el mismo día.** La acción
`avisar.responsable` (§0.j) deja el aviso en `agente.av_agent_avisos` con el
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

---

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

---

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

---

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

---

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

**Ahora se persisten** (`jobs/db_tamano` → `agente.av_agent_hallazgos` con
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

---

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

---

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

---

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

---

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

---

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

---

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

---

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

---

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

---

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

---

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

---

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

---

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

---

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

---

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

---

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

---

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

---

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

---

### 0.bd EL OBJETO: uno solo, y el tipo es un campo (2026-08-21)

> *«Que todo lo del AV Agent esté como objeto. Va a ser **siempre el mismo
> estilo**, solo que va a cambiar el TIPO de problema —log, aviso, etc.— pero
> **cómo van a estar es lo mismo**. Después cambiará la solución, el análisis.»*

Es la descomposición correcta, y nombra tres cosas que varían por separado y
estaban mezcladas en 22 tablas:

    LA FORMA      cómo se guarda y cómo vive        → UNA
    EL TIPO       de qué habla (bono · job · log)   → un CAMPO
    LA SOLUCIÓN   qué se hace y cómo se explica     → enchufable, por tipo

`agente.av_agent_items` + `core.ciclo.Item`. Un bono mal cargado, un job caído,
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

---

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

---

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

---

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

---

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

---

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

---

### 0.cq LOS DOS DIAGNÓSTICOS HABLARON — un árbitro para la pata, y el contrato real del control (2026-08-22)

La ronda anterior dejó dos preguntas medibles; los diags las contestaron y las
dos respuestas eran DISTINTAS de lo que parecía en pantalla:

**1. Los 11 «resueltos solos» NO estaban resueltos.** `diag_patas_master`:
columna Y blob siguen apuntando a la pata en PESOS, y no hay ninguna acción de
lote sobre ellos (el lote de las 00:26 cubrió los 6 BOPREALes + GD46, que sí
caducan bien). El «ya no aparece en el control: se resolvió solo» era el
control mintiendo por su criterio: usaba `es_default` — que es una **copia del
master** (`sembrar_especies`: `es_default = simbolo == curvas.instrumento`) —
y para estos 11 Primary tiene la default en ARS, así que el JOIN no devolvía
fila y eran INVISIBLES, no resueltos. El detector (§0.bu) ya usaba el árbitro
correcto: **la moneda del EJE** vía `core.especies.pata_para_el_eje`.
RESUELTO: `_chk_patas_equivocadas` usa el MISMO árbitro (congelado por test:
`es_default` no puede volver al predicado). Al deployar, los 11 reaparecen en
el control —que es la verdad— y la acción `mercado.apuntar_pata` se
desbloquea: se aplican desde la pantalla y ahí sí caducan de LA LISTA.
De paso el control cubre TODOS los ejes declarados (una curva ARS suscribiendo
la pata D también es un campo mal cargado).

**2. La alerta del sábado por las tenencias era FALSA, y el user tenía razón
en el porqué.** `diag_tenencia_fechas`: todos los viernes históricos están
(14/08, 07/08) y el máximo un sábado es el JUEVES — el job corre L-V a las 11
UTC y **escribe el hábil anterior a su corrida** (T-1). `fecha_objetivo`
calculaba «hábil anterior a HOY», que un sábado exige el viernes… que recién
se escribe el lunes. RESUELTO en dos pasos con el calendario: (1) ¿cuál fue la
última corrida esperada? (hoy solo si es hábil y su `corre_utc` ya pasó, con
una hora de gracia; si no, el hábil anterior); (2) esa corrida escribe el
hábil anterior a sí misma. La hora del cron se declara en `REHACIBLES`
(`corre_utc`), al lado del job — el dato vive con el job, no con el que
pregunta. Bonus del diag: **06/08 también falta** (un hueco histórico real,
nadie lo rehizo) y `job_runs` registra este job como tipo `aum`.

La lección que se repite y ya tiene nombre: **las dos pantallas del mismo
hecho tienen que leer EL MISMO predicado** — es REGLA #9 y es la tercera vez
esta semana (voto, símbolo, pata).

---

### 0.cr CADA SUB-TAB DECLARA SU CICLO — y QUÉ PIDE ALGO se vuelve el puente (2026-08-22)

> *«No entiendo cómo funciona, qué tiene que pasar acá, qué esperar de esto,
> cómo hacer para que salga algo. QUÉ PIDE ALGO es la peor: un número
> altísimo, no se puede hacer nada y figuran unas pares nada más. Hay que
> darle un sentido a este modal — no hay conexiones entre las cosas.»* — user

El diagnóstico es justo: cada sub-tab mostraba DATOS sin declarar su CICLO
(qué la llena, qué la vacía, qué hace uno ahí). El sentido que queda, escrito
en cada pantalla y congelado acá:

    LA LISTA        el BANCO DE TRABAJO: acá están los botones. Se vacía
                    arreglando, votando o descartando.
    QUÉ PIDE ALGO   la PRIORIZACIÓN sobre la memoria completa. No tiene
                    botones A PROPÓSITO: **resume por CAUSA y cada causa es
                    un CLIC que te deja en LA LISTA filtrada** (y cada sujeto,
                    buscado). Se vacía sola cuando los detectores dejan de ver
                    las filas. Lo truncado se dice («las 25 más urgentes de
                    54 — el resumen por causa sí está completo»).
    ¿AGUANTAN?      un RELOJ, no una lista de trabajo: nada que apretar. El
                    número del menú son CAUSAS en prueba (no 213 casos — el
                    lote de 133 patas es UN arreglo con un solo reloj); los
                    casos van al pie. Sale solo: 30 días hábiles sin volver →
                    voto «verificado» y desaparece; si vuelve → AHORA.
    VIGILANCIA      el backlog del monitor en vivo. Se cierra solo cuando el
                    monitor deja de verlo en una pasada que evaluó su tipo;
                    «visto» solo lo saca de "sin ver".

Y para poder verlo sin pantalla: **`scripts/diag_modal`** imprime las tres
sub-tabs (QUÉ PIDE ALGO con sus grupos, ¿AGUANTAN? por causa, VIGILANCIA por
regla) desde las MISMAS funciones que dibuja el navegador, cada una con su
«cómo sale algo de acá» — la familia queda: `diag_ahora` (AHORA) ·
`diag_encontro` (el censo de LA LISTA) · `diag_modal` (las otras tres).

---

### 0.cv IGNORAR es un SNOOZE del día, «viejo» cierra solo, y el diagnóstico da UNA respuesta (2026-08-22)

El round de correcciones más grande de la semana, todo del mismo mensaje del
user. Cinco decisiones que quedan:

**1. El modelo mental canónico de las pantallas.** AHORA es el **noticiero**:
el primer lugar donde aparece lo que se detectó, sin accionables. ENCONTRÓ →
LA LISTA es el **banco de trabajo**: el mismo aviso pero con el toolkit, y
SOLO para lo que tiene algo que hacer de nuestro lado. Lo que no se arregla
desde acá (AUNESA caído, un motor ajeno) NO ensucia LA LISTA: es un objeto con
estado en VIGILANCIA que se cierra solo cuando el monitor deja de verlo — y su
aparición/cierre se cuenta en AHORA. Esto YA era así (verificado: el centinela
escribe en `av_agent_centinela`, no en la foto de la relevada) y queda escrito
como contrato.

**2. IGNORAR = «sacalo de la lista por HOY», no una blacklist.** El user:
*«si lo ignoro quiero que salga de ENCONTRAR — NO que entre en una blacklist
de cosas que nunca más me van a interesar. Si el agente funciona bien, mañana
lo vuelve a detectar y TIENE que volver a aparecer»*. El botón de un hallazgo
ya NO escribe `agente.av_agent_ignorados`: mueve los objetos del sujeto a
`ignorado` (acción `ignorar_hoy`) y **el snooze vence solo** —
`av_agent_items.ver()` reabre como `nuevo` (no `volvio`: nadie lo dio por
arreglado) la primera vez que un detector lo re-ve en un día ART posterior.
La vista lo marca (`ignorado`, contador `ignorados_hoy`) y la pantalla lo
esconde contándolo — nunca en silencio. **La tabla durable queda SOLO para la
respuesta «no nos interesa» de las preguntas de alta**: ese sí es un juicio
sobre el PAPEL, sigue siendo reversible en DECIDIDO, y son dos gestos
distintos a propósito. Congelado por `test_av_agent_ignorar_hoy`.
⚠️ Lo ignorado ANTES de este cambio quedó en la blacklist vieja: se ve y se
deshace en DECIDIDO → NO TE INTERESAN.

**3. El voto «¿te sirve verlo?» se eliminó de la UI.** El user: *«no le
encuentro el sentido — todo me sirve ver, el agente ya muestra en función de
lo que le pido»*. Tenía razón: en una observación no hay diagnóstico que
juzgar y el voto de utilidad generaba métricas que no miden nada. Para sacar
una fila está IGNORAR (por hoy); los votos viejos siguen valiendo como dato.
**El «¿acertó el diagnóstico?» se queda**: ese sí entrena la compuerta.

**4. Un desenlace «viejo» CIERRA el hallazgo en el acto** (caso GD46: «dice
arreglado hoy pero sigue figurando»). Dos fixes encadenados: (a) la conclusión
de las ocho lentes («causa: sano») viaja en capa `veredicto` y `_desenlace`
solo buscaba `nada_que_hacer` entre las `prueba` — por eso un cotejo ámbar por
diferencia de DEFINICIÓN (paridad clean vs precio operado) le ganaba al «no se
detecta nada roto» y el título decía «LEÉ LA TRABA» sobre un bono sano; ahora
el flag se busca en todas las capas y viejo gana. (b) `_cerrar_si_viejo` corre
tras CADA `out["veredicto"]` (4 puntos, contados por test): marca los objetos
del sujeto `resuelto` (motivo `diagnostico_probo_sano`) — las lentes re-corren
la MISMA detección que el detector nocturno, así que esperar a la noche para
cerrar era burocracia. El front esconde ARREGLAR y dice «quedó VIEJO — se
cerró solo». Si el diagnóstico se equivocó, el detector lo re-ve → VOLVIÓ.

**5. El diagnóstico da UNA respuesta; el razonamiento es del agente.** El
user: *«¿para qué LOS PASOS, CONTEXTO, PARA APRENDER? Eso es interno del
agente. Yo quiero: ¿está ok? → arreglar; ¿no está ok? → el motivo exacto»*.
La pantalla queda: ORDEN imperativa → la traba UNA sola vez (el título del
desenlace ya no se repite arriba del bloque que dice lo mismo) → y TODO lo
demás (pasos, contexto, lecciones, contadores, cómo se calculó) detrás de
«▸ detalle interno del agente», cerrado por default. No hizo falta el LLM que
el user ofreció: los textos ya eran prosa razonable — el problema era la
repetición y la jerarquía, que se arreglan en la estructura y no gastan un
token por diagnóstico. Si con esto todavía no alcanza, el paso siguiente es
una redacción LLM de la conclusión (tarea nueva → pasa por la ley de §0.o).

**Y DECIDIDO se rediseñó** (§0.cr aplicado acá): una sola columna en el orden
en que uno pregunta — CONTESTADAS SIN EJECUTAR TODAVÍA (dice explícito que se
mueve sola cuando el agente gane la habilidad), NO TE INTERESAN (lo único con
botón: deshacer), REGISTRO DE RESPUESTAS (ex «HISTORIAL», que era un historial
adentro del historial), VOTASTE. Cada bloque dice qué es y si se actualiza.

---

### 0.cw EL DNI SE RESPETA DE PUNTA A PUNTA — tres flujos lo ignoraban (2026-08-22)

> *«Cada cosa que pasa no es un objeto con un ID… no hay un DNI: existe un
> problema y puede aparecer infinitamente en el día por más que ya lo
> soluciones. A nivel base y a nivel desarrollo claramente está todo mal.»*

El diagnóstico del user es correcto con una precisión: el DNI **existe**
(`agente.av_agent_items`, clave = sujeto|causa, §0.bd) — lo roto era que tres
flujos actuaban sobre fotos o strings sin consultarlo. Los tres, con su caso:

**1. La identidad se stripeaba, y el upsert fabricaba FANTASMAS (caso OTC).**
La cadena completa del bug, leída en el código: el control `assets_sin_cartera`
canta la `unidad` EXACTA (`'[OTC - MAI.ROS/ENE27] '` — con espacio final; el
doble espacio en pantalla era la pista) → `av_agent_hacer._sujeto()` hacía
`.strip()` → la propuesta nacía con OTRA identidad → `assets_sql.set_campos`
es un **UPSERT**, así que aplicar no falló: **creó una fila nueva** trimmeada
con `CARTERA=DERIVADOS` → la verificación releyó esa fila («✔ CARTERA =
DERIVADOS») → el control siguió cantando la fila real, y QUÉ PROPONÉS volvía a
proponer lo mismo, infinito. Tres pantallas coherentes, ninguna diciendo la
verdad — REGLA #9 en su forma más pura. **Arreglos**: `_sujeto()` ya no
stripea (la identidad es el string exacto; para mostrar está `_nombre()`), y
las escrituras del agente van con `set_campos(..., crear=False)` — sobre una
unidad que no existe EXACTA se levanta error en vez de inventar un asset.
**Lo que quedó en la base se mide y repara con
`scripts/diag_unidades_fantasma`** (dry-run; `--reparar` copia la cartera a la
fila real y borra la fantasma, con tres guardas: btrim-colisión, escrita por
av-agent, sin tenencia).

**2. «Probado sano» marcaba `resuelto` y generaba VOLVIÓ espurio (caso GD46).**
El arreglo se escribe en `mercado.curvas`, pero el detector lee la TEA de
`market_snapshot`, que recién cambia cuando `motor_curvas` se reinicia. Cerrar
como `resuelto` hacía que el siguiente avistaje legítimo lo marque «volvió» —
ensuciando la señal más valiosa del modelo con falsas reincidencias. Ahora
`resolver_sujeto` y el `_cerrar_viejo` del masivo marcan **`en_curso`**: la
fila sale de la lista igual (atendida), un re-avistaje NO la mueve (suma
`veces`), y la cierra el DETECTOR vía `_cerrar_ausentes` cuando deja de verla
— el agente no califica su propio trabajo, que es la filosofía que
`_mover_item` ya tenía escrita.

**3. Los lotes trabajaban sobre la FOTO sin mirar el objeto.** El masivo
tomaba los casos de la foto de la última corrida y re-diagnosticaba (y el lote
re-aplicaba: dos `arreglar_bono` GD46 en el libro, 19:10 y 20:05). Ahora
`arrancar()` consulta `estados_de()` (una query para todo el lote) y saltea
en_curso/resuelto/ignorado **diciendo cuántos** (`saltados_atendidos`). Y la
cola de DECIDIDO se concilia contra la BASE: una `alta` contestada cuyo ticker
ya existe en `mercado.curvas` se sella `aplicada` sola con nota «ya estaba en
la base» (read-repair en `pendientes_de_aplicar`) — la cola muestra lo que de
verdad falta, no promesas que la realidad ya cumplió.

**Y el error de UX del round anterior se revierte**: los pasos con panel de
acción (`hacer`) se muestran SIEMPRE fuera del pliegue «detalle interno» — lo
que se pliega es el razonamiento, jamás la acción. Congelado por
`test_av_agent_dni`.

**Medido en prod (diag del user, 2026-08-23): la hipótesis dio exacta.** Dos
fantasmas — `'[OTC - DLR052027]'` y `'[OTC - MAI.ROS/ENE27]'` trimmeadas,
escritas por `av-agent` el 22/08 con cartera y cero tenencias — al lado de las
reales con espacio final (de `job:assets_autofill`, con 6 y 16 tenencias, sin
cartera). Reparado con `--reparar`. Y quedó la PREVENCIÓN: el control nocturno
**`unidades_gemelas`** canta cualquier colisión futura de unidades por btrim,
venga de donde venga — la próxima dura horas, no días.

---

### 0.cx LA LEY DE CONEXIÓN — y el HISTORIAL que la estrena (2026-08-23)

> *«Si hay que hacer una nueva regla general del proyecto que sea una ley
> irrompible: todo lo nuevo que se desarrolle en el AV AGENT no tiene que
> estar suelto como si nada — acá todo se tiene que conectar.»*

Quedó como **REGLA #10 en CLAUDE.md** (las cinco: objeto con DNI · una casa
por naturaleza · fecha y hora visibles · consultar el DNI antes de actuar ·
declararse en los registros). Lo que la estrena, todo del mismo mensaje:

**Las NOTICIAS salen de ENCONTRÓ.** «LA BASE CAMBIÓ: 34» ocupaba LA LISTA sin
un solo botón y sin hora — y ENCONTRÓ es accionable por definición. Nace
`av_agent.TIPOS_NOTICIA` (`db_cambio`, `tabla_quieta` — observaciones de la
base sin accionable, DECLARADO como `EN_AHORA_SIEMPRE`, nunca inferido): la
vista las marca `noticia`, LA LISTA las esconde contándolas (la búsqueda las
encuentra), y su casa es **AHORA → NOTICIAS DE LA BASE**, cada una con fecha y
hora, sumando al contador de la tab.

**HISTORIAL queda en DOS cosas con el menú horizontal de ENCONTRÓ:**

- **REGISTRO** — «¿por qué DECIDIDO no está en YA HIZO?» No había razón: lo
  que escribió el agente y lo que decidiste vos (respuestas, votos) son
  eventos del MISMO sistema, y separarlos obligaba a mirar dos tablas para
  reconstruir una historia. Ahora es UNA línea de tiempo ordenada, cada
  evento con fecha+hora, quién y sobre qué. Arriba, lo único vivo:
  CONTESTADAS SIN EJECUTAR (que se concilia contra la base, §0.cw) y NO TE
  INTERESAN (el único botón: deshacer). Y **sin grillas de columnas fijas**
  — la `grid-cols-[110px_170px_90px_1fr]` que montaba «JOB:CIERRE_CANJE»
  sobre la columna de al lado se fue: dos renglones que envuelven.
- **COMUNICACIONES** — ex «MANDÓ», que no es una palabra de nadie. **Solo las
  de HOY** (día ART), con fecha y hora por fila: una comunicación es del día;
  el efecto pendiente vive en la bandeja del destinatario (/api/avisos), no
  acumulándose acá. El pasado no se pierde: las escrituras que dispararon
  esos mensajes están en el REGISTRO.

---

### 0.f El eval set (2026-08-17)

`agente.av_agent_evals` — un ✔/✖ humano por diagnóstico, con la causa correcta
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
