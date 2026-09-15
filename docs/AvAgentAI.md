# AvAgentAI — el asistente de TradingAV

Documento oficial de la IA conversacional. Todo lo que hace el asistente está
acá; el código está en `asistente/`. Doc [VIVO]: se actualiza en el mismo commit
que el código. Regla que se carga sola al tocar el paquete:
`.claude/rules/asistente.md`. Para sumar un mundo: skill `add-mundo`.

## 1. Qué es

Un asistente que contesta preguntas de la mesa sobre cuentas y mercado, en la tab
LAB del AV AGENT (`trading.acaquant.com`, admin-only). No escribe nada: solo lee.
Está hecho con LangGraph (el grafo) y LangChain (proveedores, herramientas,
mensajes). La puerta a los proveedores es `core/modelos.py`; la traza de cada
llamada, `core/traza.py`.

## 2. Arquitectura

```
pregunta ─► preparar ─► despacho ─► [cuenta] ─┐
                          │        [mercado] ─┴─► junta ─► finalizar ─► respuesta
                          └─(una regla contestó)──────────────┘
```

| Nodo | Módulo | Qué hace | Modelo |
|---|---|---|---|
| `preparar` | `memoria.py` | poda y achica el historial, sanea el foco | ninguno |
| `despacho` | `despacho.py` | reglas primero; si ninguna decide, el modelo elige mundos | `asistente_despacho` solo si las reglas no deciden |
| `cuenta` | `mundos/cuenta.py` | agente con `cobros_futuros` (hasta una fecha), `tenencia_actual` (con lo que el mercado dice de cada título) | `asistente_cuenta` (OpenAI pro, datos de negocio) |
| `mercado` | `mundos/mercado.py` | agente con `curva`, `ficha_bono` | `asistente_mercado` (DeepSeek flash) |
| `junta` | `junta.py` | con un mundo, pasa su respuesta; con varios, redacta cruzándolos | `asistente_cuenta` |
| `finalizar` | `grafo.py` + `control.py` | arma el historial de salida y corre el control de números | ninguno |

Los mundos corren en paralelo. Cada uno es un subgrafo `modelo ↔ herramientas`
con tope de 6 vueltas. **Un nodo del grafo = un módulo del paquete.** El SYSTEM
de cada agente termina con la fecha de hoy (`agente.sistema`): sin eso «hasta
fin de año» se calcula desde una fecha inventada.

## 3. El paquete

```
asistente/
  agente.py        qué es un Agente (nombre, tarea, describe, instrucción, herramientas,
                   señales, aprende_foco) y COMUN, la instrucción que todos comparten
  mundos/
    __init__.py    MUNDOS: el registro, explícito
    cuenta.py      las herramientas de cuenta + AGENTE
    mercado.py     las herramientas de mercado + AGENTE
  despacho.py      reglas (Decision), el agente DESPACHO y cómo se lee su elección
  junta.py         el agente JUNTA
  grafo.py         el StateGraph, el subgrafo de cada agente, preguntar()
  herramientas.py  función → tool (docstring = descripción, firma = esquema); TODAS, POR_NOMBRE
  memoria.py       poda, achicado, marcas por mundo, dicts ⇄ mensajes de LangChain
  estado.py        el foco: claves con lista cerrada de valores
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

## 4. Cómo corre una pregunta

«¿Qué bono CER rinde más que los que tengo en la 805?»

1. `preparar`: memoria podada a 8 turnos, resultados viejos achicados, foco saneado.
2. `despacho`: la regla de señales ve «tengo» (cuenta) y «bono», «cer», «rinde»
   (mercado): van los dos, sin llamar a ningún modelo. Evento
   `despacho · regla: señales (…)`.
3. En paralelo: `cuenta` pide `tenencia_actual(805)` y redacta; `mercado` pide
   `curva("cer")` y redacta. Cada uno con sus dos fichas, no las cuatro.
4. `junta` recibe las dos respuestas con sus datos y escribe una sola.
5. `finalizar`: historial = anterior + pregunta (marcada con sus mundos) + lo
   nuevo de cada mundo + la junta. El control busca cada número de la respuesta
   en los datos.

Respuesta al front: `respuesta`, `falta`, `error`, `estado`, `sesion` (con su
costo), `titulo`, `guardada`, `mundos`, `eventos` (cada paso, con el agente que
lo hizo), `control`, tokens y vueltas.

## 5. El despacho

Tres capas, de la más barata a la más cara:

1. **Reglas** (`despacho.REGLAS`, en orden; la primera que decide, decide):
   - `meta`: «¿qué sabés hacer?» se contesta desde los objetos `Agente`, sin
     modelo ni mundos.
   - `foco`: hay cuenta (nombrada o en foco) y ninguna señal de otro mundo →
     el mundo que aprende foco, solo.
   - `señales`: las señales de uno o más mundos aparecen en la pregunta → esos.
2. **El modelo** `asistente_despacho`, solo si ninguna regla decidió. Se acepta
   una lista de nombres y nada más.
3. **Todos**, si el modelo no se entendió o falló. Más caro, no más peligroso.

Una señal es una palabra entera, en minúsculas y sin acentos, declarada en el
`Agente` (`senales`; las variantes se declaran: «cer» no es «cerca»). Una
cuenta nombrada suma al mundo que la atiende aunque no haya otra palabra suya.
Una pregunta meta es una de las frases de `META` entera, no una frase que las
contenga. El evento `despacho` dice siempre quién decidió: `regla: …`,
`eligió el modelo` o `van todos`. Con eso se mide, en `eventos` e `ia.llamadas`,
qué parte de las preguntas no paga la llamada.

## 6. Ruteo de modelos

Una tarea = un agente = un modelo. Las tareas viven en `core/modelos.TAREAS`; se
cambian desde el panel del LAB sin deploy (se prueba el modelo antes de guardar).
Una tarea desconocida no corre.

| Tarea | Default | Datos | Por qué |
|---|---|---|---|
| `asistente_despacho` | DeepSeek flash | ninguno | clasifica, no contesta |
| `asistente_cuenta` | OpenAI pro | negocio | herramientas + no entrena |
| `asistente_mercado` | DeepSeek flash | público | herramientas |

La junta (sin herramientas) recibe el esquema `{respuesta, falta}` si el
proveedor lo soporta. Un agente con herramientas no: OpenAI, con
`response_format` en Chat Completions, exige que toda herramienta sea `strict`
(medido en el LAB: «Only `strict` function tools can be auto-parsed»), y
DeepSeek no acepta esquema. Esos contestan en prosa y marcan lo que no pudieron
con un último renglón `Falta: …` que `esquema.leer` entiende.

`datos: negocio` exige un proveedor que no entrena (`permitido_salir`), hoy
aflojado por `IA_PERMITE_PROVEEDOR_QUE_ENTRENA` (ver `docs/SECURITY.md`). Sin la
clave del proveedor, la llamada no sale y el error vuelve como dato.

## 7. Cómo agregar

Cada paso tiene un test que falla si se olvida. Si un cambio necesita tocar
`grafo.py`, algo está mal diseñado: preguntar antes.

**Un mundo** (skill `add-mundo`):
1. `asistente/mundos/<nombre>.py` con sus herramientas y un `AGENTE = Agente(...)`
   con `nombre`, `tarea`, `describe` (lo lee el despacho), `instruccion`
   (`COMUN` + lo suyo), `herramientas`, `senales`.
2. La tarea en `core/modelos.TAREAS` (tier, `datos: "negocio"` si ve cuentas,
   `usa_herramientas: True`).
3. Una línea en `mundos/__init__.py::MUNDOS`.
4. Tests en `tests/unit/test_asistente.py` con el proveedor falso, y este doc
   (§2, §3, §6).
Test: `test_todo_mundo_esta_registrado_y_declarado`.

**Una herramienta**: una función en el archivo de su mundo, agregada a la tupla
`herramientas` de su `AGENTE`.
- docstring: qué hace, qué **no** es (cuál es la otra parecida), qué devuelve.
- firma: `cuenta` sin default si es de la cuenta; listas cerradas con `Literal`.
- devuelve un dict; errores como `{"error": ...}`, nunca excepción.
- topes declarados y `truncado` cuando recorta; totales calculados en SQL.
- claves con `_` (`_tabla`) son para la pantalla y no viajan al modelo.
- toda consulta de cuentas lleva `{permitido.FILTRO_SQL}`.
Tests: techo de ficha, docstring, permiso, `_` no viaja.

**Una regla de despacho**: una función `(pregunta_normalizada, foco) -> Decision | None`
en `despacho.py`, sumada a `REGLAS` en el orden que corresponda. Se lee en una
línea o no es una regla.

**Un control antes de una herramienta**: una función en `puerta.py`, por
argumento, sumada a `CONTROLES`.

**Una clave de foco**: una entrada en `estado.EN_FOCO` con su lista cerrada.

**Un proveedor**: una fila en `core/modelos.PROVEEDORES` y su rama en `armar()`.

**Un sub-agente**: es una herramienta más de un mundo, una función que arma su
propio `Agente` y corre `grafo.subgrafo(agente).invoke(...)`. El mundo lo pide
por nombre como a cualquier herramienta y recibe su respuesta como dato. No
hay que tocar el grafo principal.

**Un cruce entre mundos** («cuánto rinden los bonos que tengo»): lo hace el
CÓDIGO, no un modelo. Un mundo expone un helper de datos (no una herramienta:
`mercado.metricas_por_ticker`) y la herramienta del otro mundo lo usa
(`tenencia_actual` trae TEA, paridad, duration y vencimiento por título). Así
ningún proveedor ve datos del otro mundo y el modelo no cruza nada a mano.

## 8. Memoria, estado y conversaciones

- Una conversación es una fila de `ia.conversaciones` (`sesiones.py`) con dueño
  (el email), título (la primera pregunta), `memoria` (lo que ve el modelo:
  dicts del proveedor, podados a 8 turnos y 300 mensajes, resultados viejos
  achicados), `foco` y `turnos` (lo que ve la persona: pregunta, respuesta,
  falta, error, mundos y las tablas que declararon las herramientas; nunca se
  poda). El ciclo (eventos) no se guarda. El navegador manda solo la pregunta y
  el id; sin id empieza una nueva. Retención 90 días sin retomar
  (`jobs/cleanup_retencion`).
- No se usa el checkpointer de LangGraph: guarda el estado interno de una
  corrida del grafo, y una corrida es una pregunta. La conversación es un dato
  del negocio, con dueño, lista y borrado: una tabla nuestra.
- Cada mensaje de la memoria lleva la marca `mundo` (cuenta, mercado; la junta
  queda como cuenta; una regla que contestó, `despacho`) y cada pregunta la marca
  `mundos` (quiénes la atendieron). Un mundo recibe solo lo marcado con su
  nombre y las preguntas que atendió: lo que trajo cuenta nunca llega al
  proveedor de mercado, y una pregunta que fue solo a cuenta no la ve mercado
  después. Un mundo que no pudo contestar deja igual su turno cerrado. Las
  marcas no viajan al proveedor.
- El foco (`estado`) es un dict aparte: hoy `cuenta`. Lo escribe el mundo cuenta,
  lo lee su instrucción. Valores fuera de la lista cerrada no entran.
- La sesión es el uuid de la conversación; cada llamada al modelo lo escribe en
  `ia.llamadas.sesion`, y de ahí sale el costo por conversación.

## 9. Seguridad

- Endpoints admin-only (`/api/agente/lab/*`). Una conversación solo la lee,
  retoma y borra su dueño.
- Cuentas: solo `ASISTENTE_CUENTAS`. Toda consulta lleva `FILTRO_SQL`; la puerta
  corta cualquier `cuenta` no habilitada antes de ejecutar; una herramienta de
  mercado no puede recibir `cuenta` (test).
- Datos de negocio solo salen por la tarea `asistente_cuenta`.
- Lo que llega del navegador (pregunta, sesión) se valida por forma.

## 10. Observabilidad

- `eventos` en cada respuesta: `pregunta`, `podado`, `achicado`, `despacho`,
  `vuelta`, `pide`, `resultado`, `estado`, `texto`, `corte`, `junta`, con `agente`.
- `ia.llamadas`: una fila por llamada con tarea, modelo, tokens, caché, latencia,
  sesión, un extracto del pedido y de la respuesta. El panel del LAB agrega por
  tarea y por conversación. El razonamiento del modelo no se guarda.
- OpenAI se llama con `store=false`: no guarda la conversación de su lado.
- `scripts/diag_herramienta.py` corre una herramienta sin modelo.
- `scripts/asistente.py` conversa desde la terminal del Droplet.

## 11. Tests

`tests/unit/test_asistente.py`. Cubren agentes, el registro de mundos, las
reglas del despacho, esquemas, permisos, puerta, control, foco, memoria, el
ruteo de modelos, la traza, las sesiones y el grafo de punta a punta con un
proveedor falso. Correr con `python -m pytest -q tests/unit/test_asistente.py`.

## 12. Lo que falta para el MVP

- Probar en el LAB con los proveedores reales: DeepSeek en mercado y despacho.
- Eval por tarea: 15 preguntas con la herramienta y los argumentos esperados,
  y con la decisión del despacho esperada.
- Alerta del AV AGENT sobre `ia.llamadas` (fallidas, latencia).
