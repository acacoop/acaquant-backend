# AvAgentAI — el asistente de TradingAV

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
| `junta` | `junta.py` | con un agente, pasa su respuesta; con varios, redacta cruzándolos | `asistente_cartera` |
| `finalizar` | `grafo.py` + `control.py` | arma el historial de salida y corre el control de números | ninguno |

Los agentes corren en paralelo. **Un nodo del grafo = un módulo del paquete.**
El SYSTEM de cada agente termina con la fecha de hoy (`agente.sistema`): sin
eso «hasta fin de año» se calcula desde una fecha inventada.

## 3. Los agentes

| Agente | Sujeto | Familia | Contesta | Herramientas hoy | Foco |
|---|---|---|---|---|---|
| `cartera` | una cuenta | | qué TIENE: títulos, nominales, valuación, rendimiento, cobros | `tenencia_actual`, `cobros_futuros` | cuenta |
| `cliente` | una cuenta | | quién ES el titular: contacto, documento, operador, segmento, estado, grupos | `ficha_cliente` | cuenta |
| `operaciones` | la mesa | | qué HIZO: boletos, volumen, aranceles; la cuenta es un filtro | ninguna todavía | cuenta, ticker |
| `renta_fija` | un bono o una curva | mercado | cuánto rinde, qué hay en una curva (filtrable por emisor), qué es, cuándo paga | `curva`, `ficha_bono` | ticker |
| `renta_variable` | una acción o un CEDEAR | mercado | cómo cotiza, cuánto varió, qué panel | ninguna todavía | ticker |
| `fondos` | un FCI | mercado | qué es, cuánto rinde, qué tiene, cuánto tarda el rescate | ninguna todavía | ticker |
| `derivados` | un futuro o una opción | mercado | dónde cotiza, tasa implícita, cadena de opciones | ninguna todavía | ticker |
| `financiamiento` | la tasa | mercado | caución por plazo, tasas de referencia | ninguna todavía | |
| `dolares` | el tipo de cambio | mercado | MEP, CCL, oficial, brechas | `tipos_de_cambio` | |

Un agente **sin herramientas** existe igual: el ruteo lo conoce, la
presentación lo lista, y si le toca una pregunta contesta «todavía no puedo
consultar esto» sin llamar a ningún modelo. Cada archivo anota las primeras
herramientas que van a entrar y sobre qué servicio existente se apoyan.

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
  puerta.py        controles antes de ejecutar una herramienta, por argumento
  control.py       control de números sobre la respuesta final
  esquema.py       {respuesta, falta}: esquema del proveedor o renglón «Falta:»
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
   pide `curva("cer")` y redacta. Cada uno con sus fichas, no las de todos.
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

Una tarea = un agente = un modelo. Las tareas viven en `core/modelos.TAREAS`; se
cambian desde el panel del LAB sin deploy (se prueba el modelo antes de guardar).
Una tarea desconocida no corre.

| Tarea | Default | Datos | Por qué |
|---|---|---|---|
| `asistente_ruteo` | DeepSeek flash | ninguno | clasifica, no contesta |
| `asistente_cartera` | OpenAI pro | negocio | herramientas + no entrena |
| `asistente_cliente` | OpenAI pro | personal | contacto y documento: nunca a quien entrena, sin texto en la traza |
| `asistente_operaciones` | OpenAI pro | negocio | el libro de la mesa |
| `asistente_renta_fija` … `asistente_dolares` | DeepSeek flash | público | toda la familia mercado |

Tres clases de dato (`core/modelos.DATOS`): vacío (nada sensible), `negocio`
(sale solo a un proveedor que no entrena, salvo el flag) y `personal` (lo
exige siempre, sin flag que valga, y `core/traza` no guarda extracto del
pedido ni de la respuesta: solo tokens y latencia).

**La TNA llega por dos vías, y donde no llega NO se deriva.** El motor publica
una sola tasa por bono, la TEA. La TNA aparece en `metrics.TNA` por dos
caminos de `curvas_vista`: `_tna_de` la calcula para `tasa_fija` (convención
de 1816 plazo-remanente, medida contra su API con error 0,00 pp), y la rama
`manda_1816` la copia del proveedor para **cualquier** curva cuando 1816 manda
(pata secundaria, o tamar no corporativo). Donde ninguna aplica, `curva`
devuelve `tna_pct: null` — **aunque la pantalla CURVAS ahí muestre un número**,
porque `bonos-table.tsx` la deriva con TEM×12 y esa convención **no está
medida** para bonos que amortizan. ⚠️ PENDIENTE: medirla contra 1816 (mismo
método que se usó para tasa fija) antes de copiarla a ningún lado. Mientras
tanto la tasa comparable de todas las curvas es `tea_pct`.

La junta (sin herramientas) recibe el esquema `{respuesta, falta}` si el
proveedor lo soporta. Un agente con herramientas no: OpenAI, con
`response_format` en Chat Completions, exige que toda herramienta sea `strict`
(medido en el LAB: «Only `strict` function tools can be auto-parsed»), y
DeepSeek no acepta esquema. Esos contestan en prosa y marcan lo que no pudieron
con un último renglón `Falta: …` que `esquema.leer` entiende.

`IA_PERMITE_PROVEEDOR_QUE_ENTRENA` afloja `negocio`, nunca `personal` (ver
`docs/SECURITY.md`). Sin la clave del proveedor, la llamada no sale y el error
vuelve como dato.

## 8. Memoria, foco y conversaciones

- Una conversación es una fila de `ia.conversaciones` (`sesiones.py`) con dueño
  (el email), título (la primera pregunta), `memoria` (lo que ve el modelo:
  dicts del proveedor, podados a 8 turnos y 300 mensajes, resultados viejos
  achicados), `foco` y `turnos` (lo que ve la persona: pregunta, respuesta,
  falta, error, agentes y las tablas que declararon las herramientas; nunca se
  poda). El ciclo (eventos) no se guarda. El navegador manda solo la pregunta
  y el id; sin id empieza una nueva. Retención 90 días sin retomar
  (`jobs/cleanup_retencion`).
- No se usa el checkpointer de LangGraph: guarda el estado interno de una
  corrida del grafo, y una corrida es una pregunta. La conversación es un dato
  del negocio, con dueño, lista y borrado: una tabla nuestra.
- Cada mensaje de la memoria lleva la marca `agente` (la junta queda como
  cartera; una regla que contestó, `ruteo`) y cada pregunta la marca
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

## 9. El tiempo (convención)

El modelo sabe qué día es porque el SYSTEM se lo dice; no calcula días. Toda
herramienta sigue la misma convención, y es lo que hay que respetar al escribir
una nueva:

- Lo que contesta **a una fecha** (una foto) toma `fecha: YYYY-MM-DD`, default hoy.
- Lo que contesta **en un período** toma `periodo` de lista cerrada (`hoy`,
  `semana`, `mes`, `anio`) más `desde` y `hasta` explícitos si el usuario dio
  fechas; el código resuelve el período contra hoy. `cobros_futuros` ya lo
  hace con `dias` y `hasta`.
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
  que más», «el que vence último», eso es un campo de la respuesta (`curva`
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

**Un control antes de una herramienta**: una función en `puerta.py`, por
argumento, sumada a `CONTROLES`.

**Un proveedor**: una fila en `core/modelos.PROVEEDORES` y su rama en `armar()`.

**Un sub-agente**: es una herramienta más de un agente, una función que arma su
propio `Agente` y corre `grafo.subgrafo(agente).invoke(...)`. No hay que tocar
el grafo principal.

**Un cruce entre agentes** («cuánto rinden los bonos que tengo»): lo hace el
CÓDIGO, no un modelo. Un agente expone un helper de datos (no una herramienta:
`renta_fija.metricas_por_ticker`) y la herramienta del otro lo usa
(`tenencia_actual` trae TEA, paridad, duration y vencimiento por título). Así
ningún proveedor ve datos del otro agente.

## 11. Seguridad

- Endpoints admin-only (`/api/agente/lab/*`). Una conversación solo la lee,
  retoma y borra su dueño.
- Cuentas: solo `ASISTENTE_CUENTAS`, para todo agente que toque una cuenta
  (cartera, cliente, y operaciones cuando tenga herramientas). Toda consulta
  lleva `FILTRO_SQL`; la puerta corta cualquier `cuenta` no habilitada antes
  de ejecutar; una herramienta de mercado no puede recibir `cuenta` (test).
- Datos de negocio solo salen por tareas `negocio`; los personales, por `personal`.
  El documento del cliente se muestra recortado a sus últimos dígitos.
- Lo que llega del navegador (pregunta, sesión) se valida por forma.

## 12. Observabilidad

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
registro, las reglas del ruteo, el foco, esquemas, permisos, puerta, control,
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
- Las herramientas de los agentes modelados sin ellas, en el orden que pida la mesa.
- Eval de HERRAMIENTAS: el del ruteo ya está (§13); falta el que fija, para cada
  pregunta, qué herramienta y con qué argumentos tiene que pedir el agente.
- Alerta del AV AGENT sobre `ia.llamadas` (fallidas, latencia).
