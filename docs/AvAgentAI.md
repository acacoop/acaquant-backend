# AvAgentAI — el asistente de TradingAV

Documento oficial de la IA conversacional. Todo lo que hace el asistente está
acá; el código está en `asistente/`. Doc [VIVO]: se actualiza en el mismo commit
que el código.

## 1. Qué es

Un asistente que contesta preguntas de la mesa sobre cuentas y mercado, en la tab
LAB del AV AGENT (`trading.acaquant.com`, admin-only). No escribe nada: solo lee.
Está hecho con LangGraph (el grafo) y LangChain (proveedores, herramientas,
mensajes). La puerta a los proveedores es `core/modelos.py`; la traza de cada
llamada, `core/traza.py`.

## 2. Arquitectura

```
pregunta ─► preparar ─► despacho ─► [cuenta] ─┐
                                   [mercado] ─┴─► junta ─► finalizar ─► respuesta
```

| Nodo | Qué hace | Modelo |
|---|---|---|
| `preparar` | poda y achica el historial, sanea el foco | ninguno |
| `despacho` | lee la pregunta y elige qué mundos hacen falta | `asistente_despacho` (DeepSeek flash) |
| `cuenta` | agente con `cobros_futuros`, `tenencia_actual` | `asistente_cuenta` (OpenAI pro, datos de negocio) |
| `mercado` | agente con `curva`, `ficha_bono` | `asistente_mercado` (DeepSeek flash) |
| `junta` | con un mundo, pasa su respuesta; con varios, redacta cruzándolos | `asistente_cuenta` |
| `finalizar` | arma el historial de salida y corre el control de números | ninguno |

Los mundos corren en paralelo. Cada uno es un subgrafo `modelo ↔ herramientas`
con tope de 6 vueltas.

## 3. Piezas

| Módulo | Rol |
|---|---|
| `agentes.py` | `Agente` (nombre, tarea, instrucción, herramientas, si aprende foco) y los declarados: `CUENTA`, `MERCADO`, `DESPACHO`, `JUNTA`. `MUNDOS` es el registro |
| `grafo.py` | el grafo principal, el subgrafo de cada agente, `preguntar()` |
| `core/modelos.py` | proveedores (`ChatOpenAI`, `ChatDeepSeek`), la tabla `TAREAS`, la elección guardada en `ia.config`, y `modelo(tarea)` que arma el `ChatModel` con su traza. `completar()` para tareas de una vuelta (el AV AGENT) |
| `core/traza.py` | `Traza`: callback de LangChain que escribe cada llamada en `ia.llamadas` (tarea, modelo, tokens, caché, latencia, sesión, error) |
| `herramientas.py` | las funciones. Docstring = descripción; firma = esquema (`Literal` → enum). `DE_LA_CUENTA`, `DEL_MERCADO` |
| `memoria.py` | poda por turnos, achicado de resultados viejos, dicts del proveedor ⇄ mensajes de LangChain |
| `estado.py` | el foco (`cuenta`): lista cerrada, lo aprende el código de una herramienta que contestó |
| `puerta.py` | controles antes de ejecutar una herramienta (cuenta habilitada). Por argumento, nunca por nombre |
| `control.py` | control de números sobre la respuesta final. Avisa, no bloquea |
| `esquema.py` | respuesta final `{respuesta, falta}` cuando el proveedor soporta esquema |
| `permitido.py` | `ASISTENTE_CUENTAS` del `.env`. Sin cuentas, no muestra nada |
| `panel.py` | gasto por tarea y por conversación, tarifas, elección de modelo por tarea |

## 4. Cómo corre una pregunta

«¿Qué bono CER rinde más que los que tengo en la 805?»

1. `preparar`: historial podado a 8 turnos, resultados viejos achicados, foco saneado.
2. `despacho` contesta `cuenta, mercado`.
3. En paralelo: `cuenta` pide `tenencia_actual(805)` y redacta; `mercado` pide
   `curva("cer")` y redacta. Cada uno con sus dos fichas, no las cuatro.
4. `junta` recibe las dos respuestas con sus datos y escribe una sola.
5. `finalizar`: historial = anterior + pregunta + lo nuevo de cada mundo + la junta.
   El control busca cada número de la respuesta en los datos.

Respuesta al front: `respuesta`, `falta`, `error`, `estado`, `sesion` (con su
costo), `titulo`, `guardada`, `mundos`, `eventos` (cada paso, con el agente que
lo hizo), `control`, tokens y vueltas.

## 5. Ruteo de modelos

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

## 6. Herramientas

Una herramienta es una función de Python en `herramientas.py`:

- docstring: qué hace, qué **no** es (cuál es la otra parecida), qué devuelve.
- firma: `cuenta` sin default si es de la cuenta; listas cerradas con `Literal`.
- devuelve un dict; errores como `{"error": ...}`, nunca excepción.
- topes declarados y `truncado` cuando recorta; totales calculados en SQL, no sumando la lista.
- claves con `_` (`_tabla`) son para la pantalla y no viajan al modelo.
- se agrega a `DE_LA_CUENTA` o `DEL_MERCADO`. El test del techo de ficha falla si el docstring se pasa.

## 7. Mundos

Un mundo es un `Agente` en `agentes.py` con su tarea en `core/modelos.TAREAS`, su
instrucción, sus herramientas y su `describe` (lo que lee el despacho). Se suma
al registro `MUNDOS`. Nada más cambia: el grafo lo enchufa solo.

## 8. Memoria, estado y conversaciones

- Una conversación es una fila de `ia.conversaciones` (`sesiones.py`) con dueño
  (el email), título (la primera pregunta), `memoria` (lo que ve el modelo:
  dicts del proveedor, podados a 8 turnos y 300 mensajes, resultados viejos
  achicados), `foco` y `turnos` (lo que ve la persona: pregunta, respuesta,
  falta, error, mundos; nunca se poda). El navegador manda solo la pregunta y
  el id; sin id empieza una nueva. Retención 90 días sin retomar
  (`jobs/cleanup_retencion`).
- No se usa el checkpointer de LangGraph: guarda el estado interno de una
  corrida del grafo, y una corrida es una pregunta. La conversación es un dato
  del negocio, con dueño, lista y borrado: una tabla nuestra.
- Cada mensaje de la memoria lleva la marca `mundo` (cuenta, mercado; la junta
  queda como cuenta) y cada pregunta la marca `mundos` (quiénes la atendieron).
  Un mundo recibe solo lo marcado con su nombre y las preguntas que atendió:
  lo que trajo cuenta nunca llega al proveedor de mercado, y una pregunta que
  fue solo a cuenta no la ve mercado después (la tomaba como pendiente). Un
  mundo que no pudo contestar deja igual su turno cerrado. Las marcas no viajan
  al proveedor.
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

`tests/unit/test_asistente.py`. Cubren agentes, esquemas, permisos, puerta,
control, foco, memoria, el ruteo de modelos, la traza y el grafo de punta a punta con un proveedor
falso. Correr con `python -m pytest -q tests/unit/test_asistente.py`.

## 12. Lo que falta para el MVP

- Probar en el LAB con los proveedores reales: DeepSeek en mercado y despacho.
- Despacho por reglas para el caso común, antes de llamar al modelo (diseño en discusión).
- Eval por tarea: 15 preguntas con la herramienta y los argumentos esperados.
- Alerta del AV AGENT sobre `ia.llamadas` (fallidas, latencia).
