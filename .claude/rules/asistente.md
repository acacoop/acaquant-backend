---
paths:
  - "asistente/**"
  - "docs/AvAgentAI.md"
  - "tests/unit/test_asistente.py"
  - "core/modelos.py"
  - "core/traza.py"
---
# EL ASISTENTE — lo que hay que saber antes de tocarlo

> **Doc única: `docs/AvAgentAI.md`** ([VIVO]: se actualiza en el mismo commit).
> Skill para sumar un agente o una herramienta: `add-agente`.

## Las palabras

**Agente** = un modelo llamando herramientas en un bucle (cartera, cliente,
operaciones, renta_fija, renta_variable, fondos, derivados, financiamiento,
dolares). Cada uno tiene un **sujeto** (una cuenta, la mesa, un bono).
**Familia** = agentes con un tema en común (mercado); no corre, agrupa.
**Ruteo** y **junta** = los dos pasos fijos del workflow, antes y después. No
son agentes: no tienen herramientas ni bucle.

## El mapa

Un nodo del grafo = un módulo. `agentes/<nombre>.py` = las herramientas de un
agente + su `AGENTE`, en un solo archivo; `agentes/__init__.py` tiene los
registros `AGENTES` y `FAMILIAS`. `ruteo.py` decide quién va (reglas primero,
modelo después), `junta.py` cruza, `grafo.py` enchufa. El resto son
piezas de un solo uso: `memoria`, `estado`, `puerta`, `control`, `esquema`,
`permitido`, `sesiones`, `panel`.

## Las invariantes (cada una tiene un test)

1. **Nada nuevo queda suelto.** TODO archivo en `agentes/` es un agente: tiene
   `AGENTE`, está en `AGENTES`, su tarea está en `core/modelos.TAREAS`, y
   tiene señales (lo compartido va en `agente.py`, no en un helper ahí). Puede
   no tener herramientas todavía: entonces contesta que no puede consultarlo,
   sin modelo. Sumar un agente NO toca `grafo.py`.
2a. **Los nombres de cartera salen de `core/cartera.py`**, nunca se tipean:
   `BONOS` (HD · ARS · DL), `RENTA_VARIABLE`, `DERIVADOS`, `FCI`, `MONEDAS`.
   Es lo que contesta «qué bonos tengo» (`cartera.TIPOS`) y lo mismo que leen
   el AV AGENT y los jobs. Un test falla si alguien vuelve a escribir uno.
2c. **Filtro ANTES del recorte, y datos sin instrucciones.** Un criterio que la
   mesa usa va como ARGUMENTO: si se recorta y después se filtra leyendo, la
   selección queda sesgada y la respuesta suena fundamentada igual. Y en el
   resultado viajan datos, no prosa para el modelo — el cómo-usar va al
   docstring o se lo repite al usuario (tests).
2d. **Lo que dibuja tabla, o es una lista larga, se PIDE** (`con_pagos`,
   `con_emisores`). La tabla es consecuencia del payload: sin el campo no hay
   tabla ni `se_muestra`, así que devolver menos limpia la pantalla sola.
2b. **Una tabla tiene sujeto, y el modelo sabe que existe.** Se declara con
   `pantalla.tabla(campo, columnas, titulo)` —el título va por firma— y el
   modelo recibe `se_muestra` en vez de los datos de dibujo: sin eso enumera
   lo mismo que la pantalla ya muestra. Tests: el título y que nadie arme el
   dict a mano.
2e. **Comparativa = ANCLA + VENTANA, y ordenar NUNCA sustituye a filtrar.**
   «Más largo», «hasta 2029» son lapsos: van como dos fechas
   (`curva(vence_desde/vence_hasta)`), filtradas antes del recorte. Si no hay
   argumento, el modelo cae en `ordenar_por` — que tiene una dirección fija y
   contesta el extremo contrario. Y cuando una punta es «lo que yo tengo», la
   pone el CÓDIGO desde el sujeto que ya tiene en la mano
   (`opciones_para_rotar(hacia_plazo=…)`): que el modelo la copie del turno
   anterior no falla, devuelve otra lista igual de convincente. Sin ancla no
   se contesta. Cuántas opciones se muestran también se declara (`cuantas`,
   default 3), no se deja en el tope técnico.
2f. **La ficha ES la interfaz: nombre + descripción + esquema.** El modelo no
   ve el código. El nombre dice qué devuelve; la descripción dice cuándo NO
   usarla y a quién le toca; el esquema lleva tipos, enums, `format: date` y
   rangos — y lo que el esquema declara NO se repite en la prosa. `_ejecutar`
   llama a la función cruda, así que pydantic no valida: el esquema evita el
   misfire, el código sigue validando. Dos herramientas que se pisan sin
   decirlo se llaman las dos, y si las dos dibujan tabla salen dos tablas de lo
   mismo (`pantalla.aviso` es preventivo por eso). Y lo que la herramienta
   puede deducir no se pregunta: con UN solo título comparable, «venderlo» no
   necesita un `ticker`.
2. **Todo dato sale de una herramienta.** El docstring ES el prompt de la
   herramienta (viaja como `description`), la firma es el esquema. La ficha
   dice QUÉ hay; el CÓMO va en `COMUN` una sola vez (test). Todo agregado
   («el promedio», «el que más») va CALCULADO sobre todas las filas, no sobre
   las que entraron en el tope. Errores como dato (`{"error": ...}`), nunca
   excepción. Claves con `_` no viajan. Las consultas de cuentas llevan
   `permitido.FILTRO_SQL`.
3. **Un agente ve solo lo suyo.** La memoria lleva marcas `agente`/`agentes`;
   lo que trajo cartera nunca llega al proveedor de renta fija. Las marcas no
   viajan. Los agentes se relacionan por el foco (`cuenta`, `ticker`): cada
   uno declara qué claves lee (`Agente.foco`), el código las aprende desde las
   herramientas.
4. **Con herramientas no viaja esquema** (OpenAI exige tools `strict`). La
   respuesta cierra con `Falta: …`; `esquema.leer` entiende las dos formas.
5. **Reglas antes que modelo.** Una regla se lee en una línea y NO ve el foco.
   Las señales genéricas de un tema van en la `Familia`, no en un agente
   (test). El evento `ruteo` dice siempre a quiénes les tocó y quién decidió.
6. **Las conversaciones tienen dueño** (`ia.conversaciones`, filtradas por
   email en cada query). El navegador manda pregunta + sesión, nada más.
7. **Una tarea = un agente = un modelo**, declarada en `core/modelos.TAREAS`;
   `datos: "negocio"` solo a proveedores que no entrenan (o con el flag);
   `datos: "personal"` nunca a quien entrena y sin texto en la traza.
8. **El tiempo es una convención** (`docs/AvAgentAI.md` §9): `fecha` para una
   foto, `periodo` de lista cerrada más `desde`/`hasta` para un rango. El
   modelo nunca calcula días.

## El eval del ruteo

`evals/ruteo.yaml` = preguntas REALES de la mesa → los agentes que TIENEN que
atenderlas. Si tocás una señal, corré `python -m scripts.eval_ruteo --sin-modelo`
y mirá la **cobertura** (que falte un agente es el error caro; que sobre, no).
Cuando el eval y el código no coinciden, manda el eval. Una pregunta nueva del
usuario va ahí, no a un test.

## Antes de pushear

`python -m pytest -q tests/unit/test_asistente.py` · `ruff check .` ·
`python -c "import api.main"` · si se tocó un endpoint, `gen_mapa_app --check`.
No hay claves de proveedor en el entorno de Claude: lo que depende del
proveedor real se prueba en el LAB, y se dice.
