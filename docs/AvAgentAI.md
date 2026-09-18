# AvAgentAI — el asistente de AcaQuant

Documento oficial de la IA conversacional. Todo lo que hace el asistente está
acá; el código está en `asistente/`. Doc [VIVO]: se actualiza en el mismo commit
que el código. Regla que se carga sola al tocar el paquete:
`.claude/rules/asistente.md`. Para sumar un agente: skill `add-agente`.

## 1. Qué es

Un asistente que contesta preguntas de la mesa sobre carteras, clientes,
operaciones y mercado, en la tab LAB del AV AGENT (`trading.acaquant.com`,
admin-only). No escribe nada: solo lee. Está hecho con LangGraph (el grafo) y
LangChain (proveedores, herramientas, mensajes). La puerta a los proveedores es
`core/modelos.py`; la traza de cada llamada, `core/traza.py`.

**Las palabras.** Un **agente** es un modelo que llama herramientas en un bucle
hasta contestar (cartera, cliente, operaciones, renta fija…). Cada agente tiene
un **sujeto** (una cuenta, la mesa, un bono). Una **familia** es un grupo de
agentes con un tema en común (mercado); no corre, agrupa. El **ruteo** y la
**junta** son los dos pasos fijos del workflow que envuelve a los agentes:
ninguno de los dos es un agente (no tienen herramientas ni bucle), son una
función antes y una llamada después.

## 2. Arquitectura

```
pregunta ─► preparar ─► ruteo ─► [cartera · cliente · operaciones]        ─┐
                         │       [mercado: renta_fija · renta_variable  ─┼─► junta ─► finalizar ─► respuesta
                         │        fondos · derivados · financiamiento    │
                         │        dolares]                               ─┘
                         └─(una regla contestó)──────────────────────────┘
```

| Nodo | Módulo | Qué hace | Modelo |
|---|---|---|---|
| `preparar` | `memoria.py` | poda y achica el historial, sanea el foco | ninguno |
| `ruteo` | `ruteo.py` | reglas primero; si ninguna decide, el modelo elige agentes | `asistente_ruteo` solo si las reglas no deciden |
| cada agente | `agentes/<nombre>.py` | su bucle modelo ↔ herramientas, tope de 6 vueltas | su tarea (§6) |
| `junta` | `junta.py` | con un agente, pasa su respuesta; con varios, redacta cruzándolos | tarea con la protección de traza más restrictiva de los participantes |
| `finalizar` | `grafo.py` + `control.py` | arma el historial de salida, corre el inspector (números y citas, con lo DADO y lo MOSTRADO) y separa las citas de la frase | ninguno |

Los agentes corren en paralelo. **Un nodo del grafo = un módulo del paquete.**
El SYSTEM de cada agente termina con la fecha de hoy (`agente.sistema`): sin
eso «hasta fin de año» se calcula desde una fecha inventada.

### 2.1 La ejecución: una pregunta que está corriendo

Una conversación y una ejecución no son lo mismo. La conversación es la
memoria durable que ve la persona; una ejecución (`run`) es el trabajo de
contestar UNA pregunta, desde que entra hasta que termina, falla o se cancela.
Todas las piezas operativas comparten un `run_id`:

| Pieza | Dueño | Para qué existe |
|---|---|---|
| `ia.conversaciones` | producto | historial, foco y turnos visibles del usuario |
| `ia.ejecuciones` | producto | dueño, estado, tiempos y resultado de cada pregunta |
| checkpointer PostgreSQL de LangGraph | motor | recuperar el estado interno y continuar desde un nodo |
| `ia.eventos_ejecucion` | producto | SSE recuperable, auditoría y duración de cada paso |
| `ia.evidencias` | producto | qué tool, sujeto, fecha y campo fundamentan una afirmación |
| `ia.llamadas` | gateway de modelos | proveedor, tokens, caché, latencia y error de cada llamada |

El checkpointer es complementario: es la fuente para **reanudar el grafo**, no
la API de negocio para listar corridas o medirlas. Su `thread_id` es el
`run_id`, así dos preguntas simultáneas de una conversación no mezclan estado.
Sus snapshots contienen el estado interno necesario para reanudar, incluidos
mensajes y resultados de herramientas; por eso pueden contener datos de
negocio o personales. Al terminar, el turno se incorpora una sola vez a
`ia.conversaciones`.

**Durabilidad y retención son capas distintas.** `ia.conversaciones` conserva
la charla visible; `ia.ejecuciones` conserva el estado y resultado de una
pregunta; el checkpointer conserva el avance interno de esa ejecución; eventos
y evidencias explican qué ocurrió y con qué datos. A los 90 días,
`jobs.cleanup_retencion` elimina cada `thread_id` mediante
`PostgresSaver.delete_thread()` y después borra `ia.ejecuciones`; eventos y
evidencias caen por FK. El borrado manual de una conversación aplica el mismo
borrado oficial a todos sus runs. No se consulta ni se acopla código propio a
`checkpoints`, `checkpoint_blobs` o `checkpoint_writes`.

Estados de una ejecución: `queued`, `running`, `waiting_approval`, `succeeded`,
`failed`, `cancel_requested`, `cancelled` y `timed_out`. Los eventos son
append-only y llevan una secuencia por ejecución: el stream puede reconectarse
con `Last-Event-ID` sin repetir ni perder pasos.

La cancelación y el timeout son cooperativos: se controlan entre llamadas al
modelo y herramientas, donde cortar no deja un pedido de tool huérfano. El
presupuesto se configura con `ASISTENTE_RUN_TIMEOUT_S` (180 s por defecto). Una
llamada HTTP en curso termina por su timeout de proveedor; el run se corta en
el siguiente límite seguro.

Toda tool atraviesa el mismo ejecutor, en este orden: resolver su ficha,
validar argumentos con el schema Pydantic que vio el modelo, autorizar contra
usuario/rol/portal/cuentas, registrar inicio, ejecutar el service dueño del
dato, extraer evidencia y registrar resultado + duración. Las clases son
`READ_PUBLIC`, `READ_BUSINESS`, `READ_PERSONAL`, `PROPOSE` y `WRITE`;
`PROPOSE` y `WRITE` nacen default-deny hasta tener aprobación humana.

## 3. Los agentes

| Agente | Sujeto | Familia | Contesta | Herramientas hoy | Foco |
|---|---|---|---|---|---|
| `cartera` | una cuenta | | qué TIENE (filtrable por tipo: bonos · acciones · fondos · derivados · caja), qué cobra, y qué opciones hay para rotar — a otro emisor, a otro tipo o a otro plazo | `tenencia_actual`, `cobros_futuros`, `alternativas_para_rotar` | cuenta |
| `cliente` | una cuenta | | quién ES el titular: contacto, documento, operador, segmento, estado, grupos | `ficha_cliente` | cuenta |
| `operaciones` | la mesa | | qué HIZO: boletos, volumen, aranceles; la cuenta es un filtro | `resumen_operaciones` | cuenta, ticker |
| `renta_fija` | un bono o una curva | mercado | cuánto rinde, qué hay en una curva (filtrable por emisor, tipo de emisor y ventana de vencimiento), qué es, cuándo paga | `instrumentos_de_la_curva`, `ficha_bono` | ticker |
| `renta_variable` | una acción o un CEDEAR | mercado | cómo cotiza, cuánto varió, qué panel | `panel_cedears` | ticker |
| `fondos` | un FCI | mercado | qué es, cuánto rinde, cuánto tarda el rescate | `ranking_fondos`, `ficha_fondo` | |
| `derivados` | un futuro o una opción | mercado | dónde cotiza, tasa implícita, cadena de opciones | `curva_futuros_dolar`, `cadena_opciones` | ticker |
| `financiamiento` | la tasa | mercado | caución por plazo, TAMAR y BADLAR | `cauciones_vigentes`, `tasa_referencia` | |
| `dolares` | el tipo de cambio | mercado | MEP, CCL, oficial, brechas | `tipos_de_cambio` | |

Los nueve agentes registrados tienen al menos una herramienta. Cada nueva
función se agrega a `AGENTE.herramientas`; `herramientas.py` la convierte en
una tool de LangChain y `grafo.py` construye su nodo de herramientas sin un
registro adicional.

| Agente | Servicio dueño del dato | Qué agrega la tool |
|---|---|---|
| `operaciones` | `api/services/operaciones_sql.py` | permiso por `id_cuenta`, ventana y contrato para el modelo |
| `renta_variable` | `api/services/scanner_sql.py` | selección individual o ranking antes del tope |
| `fondos` | `api/services/fci_sql.py` | filtros, resolución inequívoca y porcentajes legibles |
| `derivados` | `api/services/mercado_hist_sql.py`, `opciones_sql.py` | proyección compacta de tasas y griegas ya calculadas |
| `financiamiento` | `api/services/mercado_hist_sql.py`, `macro_sql.py` | plazo de caución y contexto histórico de TAMAR/BADLAR |

La tool no contiene SQL ni una segunda fórmula: adapta el contrato del
servicio al modelo. La excepción de seguridad es `operaciones_sql`: su API se
amplió con `scope`, `cuenta` e `instrumento` para que el permiso se aplique
antes de agrupar y truncar.

**Cada agente tiene su sujeto.** Cartera y cliente hablan de UNA cuenta (la
llave `cuenta` es obligatoria en sus herramientas). Operaciones habla de la
mesa: la cuenta es un filtro opcional, como el título o la fecha. Los de mercado
hablan de un instrumento: no tienen cuenta. Los agentes se relacionan por el
foco (§8): la llave que quedó en la mano sirve para todos los que la leen.

## 4. El paquete

```
asistente/
  agente.py        qué es un Agente (nombre, tarea, describe, instrucción, herramientas,
                   señales, familia, claves de foco), qué es una Familia, y COMUN
  agentes/
    __init__.py    AGENTES y FAMILIAS: los registros, explícitos
    cartera.py     cliente.py     operaciones.py
    renta_fija.py  renta_variable.py  fondos.py  derivados.py  financiamiento.py  dolares.py
  ruteo.py         las tres capas: reglas (Decision), la instrucción del modelo
                   y cómo se lee su elección. No es un agente: no tiene bucle
  junta.py         la instrucción y la tarea de la junta. Tampoco es un agente
  grafo.py         el StateGraph, el subgrafo de cada agente, preguntar()
  herramientas.py  función → tool (docstring = descripción, firma = esquema); TODAS, POR_NOMBRE
  memoria.py       poda, achicado, marcas por agente, dicts ⇄ mensajes de LangChain
  estado.py        el foco: claves con normalizador y validador
  evidencia.py     el contrato herramienta↔evidencia: de quién es cada dato (`_sujeto`)
  control.py       el inspector: números y citas de la respuesta final, con explicación
  esquema.py       {respuesta, falta}: esquema del proveedor o renglón «Falta:»
  pantalla.py      el contrato tabla↔pantalla: cómo se declara, qué dibuja la
                   pantalla y qué se le avisa al modelo que ya se dibujó
  permitido.py     ASISTENTE_CUENTAS (.env, fail-closed)
  sesiones.py      conversaciones guardadas (ia.conversaciones)
  panel.py         gasto, tarifas y elección de modelo por tarea
```

Fuera del paquete: `core/modelos.py` (proveedores, `TAREAS`, `modelo(tarea)`),
`core/traza.py` (una fila en `ia.llamadas` por llamada), `api/routers/agente.py`
(`/api/agente/lab/*`), `scripts/asistente.py` (la terminal), `scripts/diag_herramienta.py`.

## 5. Cómo corre una pregunta

«¿Qué bono CER rinde más que los que tengo en la 805?»

1. `preparar`: memoria podada a 8 turnos, resultados viejos achicados, foco saneado.
2. `ruteo`: la regla de señales ve «tengo» (cartera) y «bono», «cer»
   (renta_fija): van los dos, sin llamar a ningún modelo. Evento
   `ruteo · regla: señales (…)`.
3. En paralelo: `cartera` pide `tenencia_actual(805)` y redacta; `renta_fija`
   pide `instrumentos_de_la_curva("cer")` y redacta. Cada uno con sus fichas, no las de todos.
4. `junta` recibe las dos respuestas con sus datos y escribe una sola.
5. `finalizar`: historial = anterior + pregunta (marcada con sus agentes) + lo
   nuevo de cada agente + la junta. El control busca cada número de la
   respuesta en los datos. Cartera dejó `cuenta = 805` en foco; renta fija no
   dejó ticker porque pidió una curva, no un bono.

Respuesta al front: `respuesta`, `falta`, `error`, `estado`, `sesion` (con su
costo), `titulo`, `guardada`, `agentes`, `eventos` (cada paso, con el agente
que lo hizo), `control`, tokens y vueltas.

## 6. El ruteo

Quién atiende la pregunta. **No es un agente**: no tiene herramientas ni bucle.
Es una función pura de la pregunta (`ruteo.por_reglas`) más, si hace falta, UNA
llamada a un modelo. Devuelve una `Decision` con tres formas posibles, y el
campo `tipo` dice cuál es (no se deduce de qué campo vino lleno):

| `Decision.tipo` | Qué significa | Qué hace el grafo |
|---|---|---|
| `van` | estos agentes, seguro | los corre en paralelo, sin modelo |
| `contesta` | la respuesta ya está | la devuelve; ningún agente corre |
| `elige_el_modelo` | es de esta familia, falta cuál | llama al modelo entre esos candidatos |

Tres capas, de la más barata a la más cara:

1. **Las reglas** (`ruteo.REGLAS`, en orden; la primera que decide, decide).
   Son código: sin costo, sin latencia, y el motivo queda escrito.
   - `regla_meta`: «¿qué sabés hacer?» → `contesta`, con la presentación armada
     desde el registro de agentes. Sin modelo y sin agentes.
   - `regla_senales`: las señales propias de uno o más agentes aparecen en la
     pregunta → `van` esos. Si solo aparecen señales **de una familia**
     («cuánto rinde», «cómo cotiza», «a cuánto vence») → `elige_el_modelo`
     entre los agentes de esa familia y nadie más.
2. **El modelo** `asistente_ruteo`, solo si ninguna regla decidió del todo.
   Recibe el foco («la conversación viene hablando de cuenta = 805»), porque una
   pregunta corta sobre una cuenta puede ser de cartera, de cliente o de
   operaciones, y elegir entre esos es suyo. Se acepta una lista de nombres y
   nada más: una frase no se interpreta.
3. **Todos los candidatos**, si el modelo no se entendió o falló. Más caro, no
   más peligroso: cada agente sigue viendo solo lo suyo.

Una señal es una palabra entera, en minúsculas y sin acentos, declarada en el
`Agente` (`senales`; las variantes se declaran: «cer» no es «cerca»). Las
genéricas de un tema («rinde», «cotiza», «precio», «tasa», «vence») viven en la
`Familia`, no en un agente, y un test falla si un agente las repite. Una
pregunta meta es una de las frases de `META` entera. El evento `ruteo` dice
siempre a quiénes les tocó (`elegidos`) y quién decidió (`motivo`: `regla: …`,
`eligió el modelo` o `van todos`).

## 7. Ruteo de modelos

Una tarea = un lugar del sistema que le habla a un modelo. Las tareas viven en
`core/modelos.TAREAS` y declaran solo lo INTRÍNSECO: para qué son, tokens,
timeout, si usan herramientas y si su traza guarda texto. Una tarea desconocida
no corre.

**Con QUÉ corre cada tarea lo decide el panel del LAB («con qué corre cada
cosa»), no el código.** La elección se guarda en `ia.config`
(`tarea:<nombre>` = `proveedor/modelo`) después de probar que el modelo
contesta (y pide una herramienta si la tarea las usa). Sin elección guardada
corre `PROVEEDOR_DEFAULT` con el tier de la fila: es un arranque, no una
opinión. El código no nombra un modelo en ningún otro lado, y ninguna regla
del gateway niega un proveedor: la vieja distinción «datos de negocio solo a
quien no entrena» se sacó por decisión del user.

Lo único que queda de aquello es `traza_sin_texto` (hoy,
`asistente_cliente` y `asistente_junta_personal`): `core/traza` guarda tokens
y latencia pero no el pedido ni la respuesta, porque llevan datos de una
persona.

**La TNA llega por dos vías, y donde no llega NO se deriva.** El motor publica
una sola tasa por bono, la TEA. La TNA aparece en `metrics.TNA` por dos
caminos de `curvas_vista`: `_tna_de` la calcula para `tasa_fija` (convención
de 1816 plazo-remanente, medida contra su API con error 0,00 pp), y la rama
`manda_1816` la copia del proveedor para **cualquier** curva cuando 1816 manda
(pata secundaria, o tamar no corporativo). Donde ninguna aplica, `instrumentos_de_la_curva`
devuelve `tna_pct: null` — **aunque la pantalla CURVAS ahí muestre un número**,
porque `bonos-table.tsx` la deriva con TEM×12 y esa convención **no está
medida** para bonos que amortizan. ⚠️ PENDIENTE: medirla contra 1816 (mismo
método que se usó para tasa fija) antes de copiarla a ningún lado. Mientras
tanto la tasa comparable de todas las curvas es `tea_pct`.

La junta (sin herramientas) toma la protección de traza más restrictiva de los
agentes participantes. Si participa `cliente`, usa `asistente_junta_personal`,
no guarda extractos de entrada ni respuesta y su salida queda marcada para que
no la relea un agente sin esa protección. Recibe el esquema `{respuesta, falta}` si
el proveedor lo soporta. Un agente con herramientas no: OpenAI, con
`response_format` en Chat Completions, exige que toda herramienta sea `strict`
(medido en el LAB: «Only `strict` function tools can be auto-parsed»), y
DeepSeek no acepta esquema. Esos contestan en prosa y marcan lo que no pudieron
con un último renglón `Falta: …` que `esquema.leer` entiende.

Sin la clave del proveedor, la llamada no sale y el error vuelve como dato.

## 8. Memoria, foco y conversaciones

- Una conversación es una fila de `ia.conversaciones` (`sesiones.py`) con dueño
  (el email), título (la primera pregunta), `memoria` (lo que ve el modelo:
  dicts del proveedor, podados a 8 turnos y 300 mensajes, resultados viejos
  achicados), `foco` y `turnos` (lo que ve la persona: pregunta, respuesta,
  falta, error, agentes y las tablas que declararon las herramientas; nunca se
  poda). El ciclo (eventos) no se guarda. El navegador manda solo la pregunta
  y el id; sin id empieza una nueva. Retención 90 días sin retomar
  (`jobs/cleanup_retencion`).
- El checkpointer de LangGraph sí se usa para reanudar una pregunta durable
  desde un nodo; no reemplaza la conversación. La conversación es un dato del
  negocio, con dueño, lista y borrado en una tabla nuestra.
- Cada mensaje de la memoria lleva la marca `agente` (la junta queda con un
  agente de su clasificación más restrictiva; una regla que contestó,
  `ruteo`) y cada pregunta la marca
  `agentes` (quiénes la atendieron). Un agente recibe solo lo marcado con su
  nombre y las preguntas que atendió: lo que trajo cartera nunca llega al
  proveedor de renta fija, y una pregunta que fue solo a cartera no la ve
  después. Un agente que no pudo contestar deja igual su turno cerrado. Las
  marcas no viajan al proveedor.
- **El foco** (`estado`) es lo que la conversación tiene en la mano, aparte de
  lo que se dijo. Dos claves hoy, `cuenta` y `ticker`, cada una con su
  normalizador y su validador (`estado.EN_FOCO`). Cada agente declara qué
  claves lee y aprende (`Agente.foco`); el código las aprende desde los
  argumentos de una herramienta que contestó. Así se relacionan los agentes:
  «¿qué tiene la 805?» y después «¿quién la atiende?» no repite la cuenta;
  «¿qué es el AL30?» y después «¿cuánto se operó hoy?» no repite el título.
  Si dos agentes en paralelo aprenden valores distintos de la misma clave,
  queda el del último en terminar: el foco es una ayuda, no una verdad.
- La sesión es el uuid de la conversación; cada llamada al modelo lo escribe en
  `ia.llamadas.sesion`, y de ahí sale el costo por conversación.
- `sesiones.preguntar()` toma un advisory lock PostgreSQL por sesión antes de
  cargar y lo libera después de guardar. Así dos runs, la CLI o futuras
  réplicas no pueden sobrescribir memoria, foco y turnos con una lectura vieja;
  el `run_id` sigue evitando duplicar un turno al reintentar.

## 9. El tiempo (convención)

El modelo sabe qué día es porque el SYSTEM se lo dice; no calcula días. Toda
herramienta sigue la misma convención, y es lo que hay que respetar al escribir
una nueva:

- Lo que contesta **a una fecha** (una foto) toma `fecha: YYYY-MM-DD`, default hoy.
- Lo que contesta **en un período** toma `periodo` de lista cerrada (`hoy`,
  `semana`, `mes`, `anio`) más `desde` y `hasta` explícitos si el usuario dio
  fechas; el código resuelve el período contra hoy. `cobros_futuros` ya lo
  hace con `dias` y `hasta`.
- Lo que se busca **en un lapso** toma una VENTANA de dos fechas
  (`instrumentos_de_la_curva(vence_desde=…, vence_hasta=…)`). Nunca se sustituye por un orden:
  `ordenar_por="vencimiento"` empieza por el más corto y contesta el extremo
  contrario al que se pidió. Cuando una de las dos puntas es «lo que yo tengo»,
  esa punta **la pone el código** desde el sujeto que ya tiene en la mano
  (`alternativas_para_rotar(hacia_plazo=…)`), no el modelo desde el texto anterior.
- Toda respuesta dice de cuándo son sus datos (`fecha`, `tenencia_del`,
  `ventana`), y la instrucción común obliga a decirlo.

Lo que no se promete todavía: series históricas por instrumento («cuánto subió
esta semana»). Hay que medir qué se guarda y con qué profundidad antes.

## 10. Cómo agregar

Cada paso tiene un test que falla si se olvida. Si un cambio necesita tocar
`grafo.py`, algo está mal diseñado: preguntar antes.

**Un agente** (skill `add-agente`):
1. `asistente/agentes/<nombre>.py` con sus herramientas y un `AGENTE = Agente(...)`
   con `nombre`, `tarea`, `describe` (lo lee el ruteo), `instruccion`
   (`COMUN` + lo suyo), `herramientas` (puede nacer vacío), `senales`
   (específicas: las genéricas van en la familia), `familia`, `foco`.
2. La tarea en `core/modelos.TAREAS` (tier, `datos: "negocio"` o `"personal"`,
   `usa_herramientas: True`).
3. Una línea en `agentes/__init__.py::AGENTES`.
4. Tests en `tests/unit/test_asistente.py` con el proveedor falso, y este doc
   (§3, §7).
Test: `test_todo_agente_esta_registrado_y_declarado`.

**Una herramienta**: una función en el archivo de su agente, agregada a la tupla
`herramientas` de su `AGENTE`. El docstring **no es documentación: es el prompt
de esa herramienta** — viaja al modelo como `description` en cada llamada
(`herramientas.ficha`), y la firma viaja como esquema (`Literal` → `enum`, que
es la única garantía dura de que no se invente un valor).
- docstring: qué hace, qué **no** es (cuál es la otra parecida), qué devuelve.
- **la ficha dice QUÉ hay; el system dice CÓMO comportarse.** Nombrá los campos
  con trampa, no el catálogo entero (el resto lo ve en el resultado), y no
  repitas una orden que `agente.COMUN` ya da: tenerla en dos lugares es que un
  día digan cosas distintas y no falle nada. Hay un test.
- firma: la llave del sujeto sin default (`cuenta`, `ticker`); listas cerradas
  con `Literal`; el tiempo según §9.
- devuelve un dict; errores como `{"error": ...}`, nunca excepción.
- topes declarados y `truncado` cuando recorta; totales calculados en SQL.
- **todo agregado va calculado**: si la pregunta puede ser «el promedio», «el
  que más», «el que vence último», eso es un campo de la respuesta (`instrumentos_de_la_curva`
  tiene `resumen`), calculado sobre TODAS las filas antes del tope. El modelo
  no calcula, y lo que ve es una muestra: sin el campo, contesta a ojo sobre
  una parte y el número parece bien.
- claves con `_` (`_tabla`) son para la pantalla y no viajan al modelo.
- toda consulta de cuentas lleva `{permitido.FILTRO_SQL}`.
Tests: techo de ficha, docstring, permiso, `_` no viaja.

**Una familia**: una entrada en `agentes/__init__.py::FAMILIAS` con sus señales
genéricas, y `familia="..."` en cada agente que la integra.

**Una clave de foco**: una entrada en `estado.EN_FOCO` (normalizador y
validador), y `foco=(...)` en los agentes que la leen.

**Una regla de ruteo**: una función `(pregunta_normalizada) -> Decision | None`
en `ruteo.py`, sumada a `REGLAS` en el orden que corresponda. Se lee en una
línea o no es una regla: si no, es un modelo escrito a mano. Una regla NO ve el
foco — el foco no cambia de qué habla la pregunta, solo de qué cuenta; quien lo
necesita es el modelo de la capa 2.

**Un chequeo antes de una herramienta**: vive en `ejecutor._autorizar` (rol,
portal, cuenta habilitada). `puerta.py` fue eso mismo y quedó desconectado
cuando el ejecutor lo absorbió; se borró.

**Una herramienta que habla de un sujeto** (una cuenta, un bono) declara ese
sujeto en su resultado con `evidencia.sujeto("cuenta", pedida)` en la clave
`_sujeto`, igual que declara su tabla en `_tabla`. Sin eso, la evidencia de la
raíz cae a la cuenta o el ticker del ARGUMENTO. Un diccionario nunca es sujeto.

**Un proveedor**: una fila en `core/modelos.PROVEEDORES` y su rama en `armar()`.

**Un sub-agente**: es una herramienta más de un agente, una función que arma su
propio `Agente` y corre `grafo.subgrafo(agente).invoke(...)`. No hay que tocar
el grafo principal.

**Qué es un «bono», un «fondo», una «acción».** Las palabras de la mesa no se
traducen con una taxonomía ni con un diccionario de sinónimos: **son carteras de
`portafolio.assets`**, que viaja en cada posición (`cartera.TIPOS`). Bonos son
TRES (`HD`, `ARS`, `DL`) y por eso no alcanza comparar la palabra contra el
campo; el resto es uno a uno. Un título sin datos de mercado hoy **sigue siendo
lo que su cartera dice que es**: la pantalla CURVAS descarta los bonos sin ejes
acordados (`curvas_vista._armar` → `sin_clasificar`), y clasificar con esa vista
—que filtra para poder dibujar— haría que un bono de verdad dejara de serlo.

**Un cruce entre agentes** («cuánto rinden los bonos que tengo», «qué opciones
tengo para rotar de YPF a Vista»): lo hace el CÓDIGO, no un modelo. Un agente
expone un helper de datos (no una herramienta: `renta_fija.metricas_por_ticker`,
`renta_fija.es_del_emisor`) y la herramienta del otro lo usa. Un cruce entre
dominios es un `import`, no una conversación entre modelos: ningún proveedor ve
datos del otro agente y no hay una segunda llamada.

La herramienta de cruce vive en el agente del dato **más sensible** (por eso
`alternativas_para_rotar` está en `cartera` y no en `renta_fija`: necesita la
cuenta, y solo los agentes con sujeto cuenta pasan por `permitido.FILTRO_SQL`).

**La tabla es consecuencia del payload, así que lo que dibuja se pide.** Si el
campo no viene, `pantalla` no dibuja nada y el modelo tampoco recibe
`se_muestra` — no hay una segunda decisión que tomar. Por eso un bloque que
genera tabla, o que es una lista larga, va bajo un argumento y no de arriba:
`ficha_bono(con_pagos=…)` —se veían los flujos del YFCOO en una pregunta sobre
rotar, porque la ficha se pidió para saber el emisor y vino todo— y
`instrumentos_de_la_curva(con_emisores=…)`, que son ~47 entradas y 2.585 chars por llamada para
algo que solo sirve cuando vas a filtrar por uno.

**Lo que el usuario nombra, la herramienta lo tiene que poder recibir.** Si no,
el modelo lo fuerza donde puede y la respuesta sale mal sin fallar. Dos casos
medidos en el LAB, los dos en `alternativas_para_rotar`: «rotar **mi YFCOO**» no
tenía `ticker`, así que la herramienta elegía por su cuenta el que menos rinde
(salió YM38O); y «a un **corporativo**» no tenía `hacia_tipo`, así que el modelo
mandó `hacia_emisor="corporativo HD"` —que no es un emisor—, la llamada falló y
el otro agente improvisó el cruce a mano con `instrumentos_de_la_curva`.

**Los filtros van ANTES del recorte.** Si la herramienta ordena todo, corta en
`limit` y el criterio del usuario se aplica después —leyendo—, la selección sale
sesgada y la respuesta suena igual de fundamentada. Medido en el LAB: «un
corporativo HD para rotar» ordenó 125 por TEA, cortó en 15, y de esos 4 eran
corporativos: se recomendó entre 4 de ~110. Por eso un criterio que la mesa usa
tiene que poder expresarse como ARGUMENTO (`emisor`, `emisor_tipo`, `tipo`): sin
eso el modelo sobre-pide y recorta a ojo.

**Una pregunta comparativa necesita ANCLA y VENTANA — y ordenar no es
filtrar.** «Más lejano», «más corto», «que rinda más» se dicen *respecto de
algo que ya está en la conversación*. Faltaban las dos mitades. La ventana:
ningún argumento de tiempo en renta fija, así que «hasta 2029» no tenía dónde
entrar y el modelo cayó en `ordenar_por="vencimiento"` — que ordena
ASCENDENTE, o sea arranca por el más CORTO: se pidió lo más largo y salieron
45 bonos desde 2026. **Cuando la herramienta no puede recibir el filtro, el
modelo usa el orden como sustituto, y el orden tiene una dirección fija que la
mitad de las veces es la contraria.** Por eso `instrumentos_de_la_curva` toma
`vence_desde` /
`vence_hasta` (§9), filtrados antes del recorte. Y el ancla: el punto de
partida era el vencimiento del bono que el usuario tenía, que existía solo
como texto del turno anterior —el foco aprende de los ARGUMENTOS de la llamada,
no del resultado (§8)—. **Esa punta la pone el CÓDIGO, no el modelo**: en
`alternativas_para_rotar` el usuario nombra la dirección (`hacia_plazo`) y como
mucho una fecha (`hacia_vencimiento`); la otra punta sale de la referencia,
que la función ya tiene en la mano. Si el modelo tuviera que copiar esa fecha
del turno anterior, copiarla mal no fallaría: devolvería otra lista, igual de
convincente (REGLA #9). Y si no se conoce el vencimiento de la referencia, no
se contesta: «más largo» que nada no quiere decir nada.

**El modelo elige la herramienta por lo que ella dice de sí misma, no por lo
que hace: el código es invisible.** Lo único que ve es NOMBRE + DESCRIPCIÓN +
ESQUEMA, así que esas tres cosas son la interfaz real y se auditan como código:

- **Nombre**: dice qué devuelve. Uno genérico atrae llamadas que no le tocan.
- **Descripción**: dice cuándo usarla **y cuándo NO**, nombrando a la que sí
  corresponde. Dos herramientas que se pisan sin decirlo terminan llamadas las
  dos — y si las dos dibujan tabla, salen dos tablas de lo mismo.
- **Esquema**: tipos, enums, formatos y rangos. Lo que el esquema declara NO se
  repite en la prosa (los valores de un `Literal`, `format: date`, el
  `minimum`/`maximum` de un entero): repetirlo gasta contexto y, si alguna vez
  difieren, el modelo tiene dos verdades sobre el mismo argumento. Ojo: el
  grafo llama a la función CRUDA (`grafo._ejecutar` hace `fn(**args)`), no al
  `StructuredTool`, así que pydantic no valida nada en runtime — el esquema es
  para que el modelo no se equivoque, y el que valida sigue siendo el código.

**Un dato que la herramienta puede deducir no se pregunta.** `alternativas_para_rotar`
pedía «¿qué título sale?» porque el usuario había dicho «venderlo» en vez de
nombrarlo — pero la cuenta tenía UN solo bono. Con uno solo no hay ambigüedad y
el código lo resuelve; con dos o más se pregunta, porque ahí elegir por él sería
inventar. Preguntar lo que ya se sabe le quema un turno al operador.

**Cuántas opciones se presentan es una decisión, no un tope técnico.**
`alternativas_para_rotar` muestra **3** (`mostrar`, hasta 20 si piden más).
Devolver 45 alternativas a «¿hay alguno?» no es ser más completo: es no haber
contestado. Y por eso `truncado` es true **solo si pidieron un número y había
más**: mostrar las 3 mejores de 4 cuando nadie pidió un número ES la respuesta,
y avisarle al modelo que «hay más» lo manda a completar con otra herramienta —
así salió la segunda tabla medida en el LAB.

**En el resultado viajan DATOS, no instrucciones.** El modelo no distingue una
cosa de la otra: un `aviso` en prosa escrito para él («hay 125 y estás viendo
los primeros 15…») terminó repetido al usuario como «la curva está truncada: se
ven 15 de 125». Lo que es cómo-usar-la-herramienta va al **docstring**, que es
su canal; en el resultado queda el dato estructurado (`truncado`, `cuantos`).

**El presupuesto se aplica antes de serializar.** Un resultado grande nunca se
corta por caracteres después de `json.dumps`: se conservan prefijos de listas
en su orden original y se agregan `truncado`, conteos y `criterio_recorte`. La
junta recibe un único payload JSON bajo el mismo presupuesto total.

**Todo lo que haya que restar, promediar u ordenar lo hace la herramienta**, y
viaja ya resuelto (`delta_tea_pp`, `resumen`): el modelo no calcula, así que un
cruce sin los deltas hechos termina en dos párrafos pegados en vez de una
comparación.

## 11. Seguridad

- Endpoints admin-only (`/api/agente/lab/*`). Una conversación solo la lee,
  retoma y borra su dueño.
- Cuentas: solo `ASISTENTE_CUENTAS`, para todo agente que toque una cuenta
  (cartera, cliente y operaciones). Toda consulta
  lleva `FILTRO_SQL`; `ejecutor._autorizar` corta cualquier `cuenta` no
  habilitada antes de ejecutar; una herramienta de mercado no puede recibir `cuenta` (test).
- Las tareas que reciben texto personal (`asistente_cliente` y una junta en la
  que participa) usan `traza_sin_texto`: `ia.llamadas` conserva métricas, nunca
  extractos. El documento del cliente se muestra recortado a sus últimos dígitos.
- Lo que llega del navegador (pregunta, sesión) se valida por forma.

## 12. Observabilidad

- **El resultado de una herramienta tiene DOS destinatarios** (`pantalla.py`):
  el MODELO, que lo lee para redactar, y la PANTALLA, que dibuja una tabla con
  él. `_tabla` es para la pantalla y al modelo se le oculta (son instrucciones
  de dibujo, no datos), pero sí viaja `se_muestra`: una línea que dice que la
  tabla existe, de qué es y cuántas filas tiene. Sin eso, «si hay tabla no la
  enumeres» le pedía al modelo evaluar una condición sobre lo único que no
  recibe, y enumeraba lo mismo que la pantalla ya había dibujado.
- **Toda tabla declara su sujeto.** `pantalla.tabla()` exige `titulo` por firma:
  en un turno pueden correr dos herramientas y salir dos tablas pegadas; sin
  título no se sabe cuál es de cuál. Un test prohíbe armar el dict a mano.
- **Toda evidencia tiene UN sujeto escalar** (`evidencia.py`). La raíz lo toma
  de `_sujeto`, que declara la herramienta; las filas, de su propia clave
  (ticker, cartera) o heredan la raíz. Antes se adivinaba con `str()` sobre lo
  primero que pareciera un nombre y `tenencia_actual` dejó como sujeto el
  diccionario `{id_cuenta, nombre}` entero: el control exigía ese texto en la
  respuesta (imposible, 2 de 2 avisos medidos eran eso) y el nombre del
  titular quedaba en `ia.evidencias`.
- **El inspector (`control.py`) mira la misma foto que el modelo.** Su fuente
  son los resultados, la pregunta y lo DADO (las cuentas habilitadas y la
  fecha, que viven en el SYSTEM y el contexto excluye a propósito). Sin lo
  dado, acusaba de inventadas a las cuentas que el sistema mismo listaba (4
  de 4 medidos). Un número que una tabla ya MUESTRA no exige cita: la
  persona tiene la fuente a la vista. Cada hallazgo trae `significa` y
  `que_hacer`; la pantalla los muestra, no los inventa.
- **Dos canales.** El modelo escribe las citas `[E:ref:campo]` dentro de la
  frase (con herramientas atadas no hay otra forma de pedírselas). `finalizar`
  las separa: a la persona le llega la frase limpia (`respuesta`), las citas
  quedan en `control.citas` y en el historial del modelo. Un corchete en un
  texto que alguien lee es ruido.
- **Un saludo lo contesta una regla** (`ruteo.regla_saludo`), sin modelo de
  ruteo ni agente. Medido: «hola» fue al agente de clientes y costó 1.477
  tokens para preguntar qué cuenta mirar, más un aviso rojo del inspector.
- `eventos` en cada respuesta: `pregunta`, `podado`, `achicado`, `ruteo`,
  `vuelta`, `pide`, `resultado`, `estado`, `texto`, `corte`, `junta`, con `agente`.
- `ia.llamadas`: una fila por llamada con tarea, modelo, tokens, caché, latencia,
  sesión, un extracto del pedido y de la respuesta (salvo dato personal). El
  panel del LAB agrega por tarea y por conversación. El razonamiento del modelo
  no se guarda.
- OpenAI se llama con `store=false`: no guarda la conversación de su lado.
- `scripts/diag_herramienta.py` corre una herramienta sin modelo.
- `scripts/asistente.py` conversa desde la terminal del Droplet.

## 13. Tests y eval

**Tests** (`tests/unit/test_asistente.py`): cubren agentes y familias, el
registro, las reglas del ruteo, el foco, esquemas, permisos, evidencia, control,
memoria, el ruteo de modelos, la traza, las sesiones y el grafo de punta a punta
con un proveedor falso. `python -m pytest -q tests/unit/test_asistente.py`.

**Eval del ruteo** (`evals/ruteo.yaml` + `scripts/eval_ruteo.py`). Un test dice
si el código anda; el eval dice si el ruteo **decide bien**, contra preguntas
reales de la mesa. Corre el MISMO nodo que producción (`grafo.ruteo`), no una
copia. Los dos errores no cuestan lo mismo y se miden aparte:

| Métrica | Qué es | Objetivo |
|---|---|---|
| **cobertura** | ¿está el agente que hacía falta? | **100%.** Si falta, la respuesta sale, se ve bien y está incompleta: nadie se entera |
| **de más** | agentes que corrieron sin hacer falta | bajo, pero **nunca** a costa de la cobertura: es presupuesto, no corrección |
| **capa** | dónde cerró cada pregunta (1 regla · 2 modelo · 3 van todos) | cuánto cuesta rutear: la capa 1 es gratis |

```bash
python -m scripts.eval_ruteo --sin-modelo   # solo capa 1: gratis, no sale a ningún proveedor
python -m scripts.eval_ruteo                # completo: la capa 2 llama al modelo real
```

La capa 1 del eval corre también como test (gratis, en CI): una señal que se
toca y pierde una pregunta rompe la suite. Cuando el eval y el código no
coinciden, **manda el eval**: se corrige la señal del agente, no la fila.

## 14. Lo que falta para el MVP

- Probar en el LAB con los proveedores reales.
- Eval de HERRAMIENTAS: el del ruteo ya está (§13); falta el que fija, para cada
  pregunta, qué herramienta y con qué argumentos tiene que pedir el agente.
- Ampliar Operaciones con detalle de boletos solo cuando la mesa defina qué
  campos puede exponer y para qué preguntas; el consolidado ya está activo.
- Alerta del AV AGENT sobre `ia.llamadas` (fallidas, latencia).

## 15. EL DIAGNÓSTICO — por qué apareció un hallazgo, antes de tocar nada

El AV AGENT detecta (`agente/`); el DIAGNÓSTICO investiga por qué. Nació de
cuatro avisos de AHORA que decían «relanzá» o «reiniciá» y no había que hacer
ninguna de las dos: un worker que nunca latió porque su código no llama a
`core/latido` (bug, no incidente), una tabla quieta cuyo job no era relanzable,
un script que corre en la PC de oficina (fuera de alcance del Droplet), y un
detector contando runs de antes de un arreglo (ventana vieja). Lo que hacía
falta no era ejecutar: era **saber qué NO hacer**.

**La forma: cinco etapas, y solo la del medio tiene libertad**
(`asistente/diagnostico.py`):

| Etapa | Quién | Qué |
|---|---|---|
| 1 DOSIER | código | el hallazgo con su evidencia, qué SUPONE el detector (su fila del catálogo), la historia del trío (episodios en 30 días, crónico, acciones aplicadas y resultado, reincidencias), los diagnósticos anteriores, el reloj. Siempre igual: acá entra la memoria del pasado sin pedirla |
| 2 INVESTIGAR | modelo (`asistente_diagnostico_investigar`) | el agente `diagnostico` (`asistente/agentes/diagnostico.py`) con herramientas de SOLO lectura: `dosier`, `habilidad`, `planilla_job`, `journal`, `procesos`, `reloj`, `buscar_codigo`, `leer_codigo`, `leer_doc`. Mismo subgrafo, eventos y evidencias que cualquier agente; tope de vueltas del grafo |
| 3 CONCLUIR | modelo (`asistente_diagnostico_concluir`) | UNA llamada sin herramientas con esquema cerrado (`ESQUEMA`): causa, resumen, afirmaciones con cita y estado (verificado / hipótesis), acción, detalle, qué NO hacer, archivo y motivo si es código, a quién escalar. DeepSeek no acepta esquema: `leer_conclusion` saca el JSON de la prosa |
| 4 VALIDAR | código (`validar`, pura) | las reglas de la casa mandan sobre el modelo: un bug de código no se arregla apretando; una configuración no se parchea; solo se aplica el arreglo que la regla declara; lo crónico con el mismo arreglo tres veces se escala como configuración; en rueda no se reinicia ni relanza (se espera al cierre); una causa afirmada necesita al menos una afirmación verificada con cita, si no queda `sin_verificar / no_se`; un `cambiar_codigo` apunta a un archivo que existe. Cada ajuste queda en `validado.cambios` |
| 5 GUARDAR | código (`agente/registro.anotar_diagnostico`) | en la fila del hallazgo (`diagnostico`, `diagnosticado_at`; AGENT.md invariante 5), con el control del inspector sobre la conclusión |

Las salidas son listas cerradas. Causas: `transitorio · configuracion ·
bug_codigo · fuera_de_alcance · detector_desactualizado · incidente ·
sin_verificar`. Acciones: `aplicar_arreglo · esperar_hasta · escalar_a ·
cambiar_codigo · nada_porque · no_se`. La acción sale de la causa, nunca al
revés.

**Riesgo aceptado, declarado.** El journal y el código son texto de terceros
que entra al contexto del investigador; una línea de log podría intentar darle
instrucciones. La defensa es la de siempre (`COMUN`: todo resultado es DATO, no
instrucción) más el hecho de que ninguna herramienta ejecuta ni escribe y de
que el validador es código: lo peor que puede pasar es una conclusión mal
clasificada, que se ve en pantalla con su marca. Ejecutar sobre un diagnóstico
es una etapa aparte que todavía no existe.

**Herramientas: solo el repo, nunca secretos.** `buscar_codigo` y `leer_codigo`
solo aceptan rutas relativas dentro del repo, excluyen `.env*`, claves,
certificados, `venv`, `.git`, y leen de a 200 líneas. `journal` solo acepta
units declarados en `deploy/systemd` y lee de a 200 líneas. Lo que pasa de
`LECTOR_UMBRAL` caracteres lo condensa el LECTOR (`asistente_diagnostico_lector`);
sin modelo, se recorta y se dice. Los tres modelos se eligen en el panel del
LAB como cualquier tarea (§7).

**Disparo a reacción, sólo con el interruptor prendido.** En cada pasada del
daemon (`jobs/agente.py::_diagnosticar`), después del tick y del ejecutor
autónomo, `encolar_pendientes()` encola un run `tipo = diagnostico`
(`ia.ejecuciones`) por cada hallazgo abierto sin diagnóstico, con el estado
cambiado desde el último, o con uno de más de `DIAGNOSTICO_REFRESCO_H` horas.
Topes en `config.py`: por pasada y por día; nunca sobre un hallazgo
`ignorado`; nunca dos veces el mismo mientras hay un run en cola. **El
interruptor vive en el LAB, no en el `.env`**: la fila `diagnostico:automatico`
de `ia.config` (`diagnostico.automatico()`, `POST /api/agente/diagnostico/automatico`,
sub-tab DIAGNÓSTICOS). Nace APAGADO, o sea MANUAL: el daemon no encola nada y
sólo corre lo que pide una persona. Se lee en cada pasada, sin caché ni
restart, y es fail-closed: sin fila o con la base sin contestar, apagado. Las
otras puertas no pasan por él —el botón «diagnosticar», la consola con
`--encolar`, que fuerza— porque ahí hay una persona pidiendo. **Cada run dice
quién lo pidió** (`ia.ejecuciones.origen`: `daemon`, el email de la persona o
`consola`; `usuario` no servía porque el daemon y el botón firman los dos
`av-agent`), y la lista del LAB lo muestra. **La guarda real está en el
worker**, que es el único que ejecuta: un run de `origen = daemon` —o sin
origen, fail-closed— con el interruptor apagado se cancela al reclamarlo
(`worker._automatico_apagado`), sin llamar a ningún modelo. Así apagar vale
también para lo que ya estaba en cola y para un daemon que siguiera corriendo
código viejo (pasó el 2026-09-18: tres runs por pasada con el interruptor
apagado, porque `agente.service` no se había reiniciado). Lo que pidió una
persona corre siempre. El worker del asistente despacha por `tipo`: un
diagnóstico no toca `ia.conversaciones`.
Desde la consola: `scripts/diagnosticar.py` (`--hallazgo N` corre ya,
`--ver N` relee, `--pendientes`, `--encolar`). Desde el LAB también se puede
pedir en lenguaje natural («diagnosticá el hallazgo #12»): rutea por señales al
mismo agente.

**Qué NO hace, a propósito.** No ejecuta nada: recomienda. La ejecución
(arreglos, relanzar, mensajes) es otra etapa que pasa por las puertas que ya
existen (`arreglos.aplicar`, `rehacer`, `mensajes`), y llega cuando el
historial de diagnósticos muestre que las conclusiones se sostienen. Un
`cambiar_codigo` es un ticket con archivo y motivo, no un parche automático.

**Qué se ve.** En AHORA, debajo de cada hallazgo, la conclusión: causa, acción,
qué no hacer, las afirmaciones con su marca de verificado o hipótesis, y qué
ajustó el validador. Un diagnóstico con `sin_verificar` se muestra como tal:
una hipótesis marcada vale más que una certeza inventada.

**Y el ciclo que la produjo, en el LAB.** La conclusión sola no alcanza para
confiar en ella: el primer diagnóstico real gastó 100k tokens en seis vueltas y
terminó en `sin_verificar` sin que nadie pudiera ver qué había mirado ni qué
había dicho. Desde AHORA, «ver cómo lo pensó → LAB» (o «diagnosticar → LAB» si
todavía no corrió) abre `GET /api/agente/diagnostico/{id}`
(`diagnostico.traza`): los runs de ese hallazgo y el ciclo entero de uno, tal
cual quedó en `ia.eventos_ejecucion`. Para que el ciclo cuente la historia
completa, el bucle de cualquier agente emite ahora un evento `modelo` por vuelta
(`grafo.py`): qué DIJO el modelo —el texto que acompaña a un pedido de
herramienta es su razonamiento en voz alta—, cuántos tokens entraron y
salieron en esa vuelta, y qué pidió; y la etapa 3 emite `diagnostico_concluir`
con el texto crudo y si parseó. Una vuelta con texto vacío se marca: es lo que
pasó con DeepSeek y antes no se veía. Los runs son del agente (`av-agent`), por
eso esta lectura no pasa por `/lab/runs` (que filtra por dueño) y vive bajo el
router admin-only del agente. «Diagnosticar de nuevo» /
`POST /api/agente/diagnostico/{id}/pedir` encola por la misma puerta que el
disparo automático, sin topes (los topes acotan al daemon, no a quien mira) y
sin duplicar uno en cola. El front no suma ni resume: dibuja los eventos.

**Cada corrida es una «conversación» del agente, a la vista en el LAB.** La
lista «diagnósticos» (`GET /api/agente/diagnostico`, `diagnostico.listado`)
muestra una fila por run de tipo `diagnostico`, creada sola al encolarse: hora,
estado (en cola / corriendo / ok / falló), el hallazgo (habilidad, sujeto), y si
terminó, causa → acción, vueltas y tokens. Abrirla muestra su ciclo. No es una
copia en `ia.conversaciones` (REGLA #9: sería el mismo dato en dos lugares): la
fuente es `ia.ejecuciones`, leída como lista. Nació de una tarde en que el
panel decía «7 atascados» y no había forma de saber qué eran: eran runs en cola
que un solo worker, de a uno y el más viejo primero, no había llegado a tomar.
`POST /api/agente/diagnostico/runs/{run_id}/cancelar` (`diagnostico.cancelar`)
corta uno: en cola muere ya, corriendo el worker corta en el próximo paso. Solo
runs de este tipo y del agente. Y el diagnóstico automático es **MANUAL salvo
que el LAB lo prenda**: el interruptor de la sub-tab DIAGNÓSTICOS («automático:
prendido / apagado · hoy N de M») es la única forma de que el daemon encole
solo. Nació apagado a pedido del user —cada corrida gasta tokens y hasta que la
traza demuestre que vale lo que cuesta, se pide de a uno— y antes vivía en el
`.env` del Droplet, donde apagarlo requería entrar y reiniciar el daemon.
