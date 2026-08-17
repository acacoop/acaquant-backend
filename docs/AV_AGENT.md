# AV AGENT — el primer agente de ACAquant  ⟨VIVO⟩

> **REGLA DE ESTE DOCUMENTO.** Es un doc **VIVO** con changelog obligatorio, igual
> que `QUANTAI.md` y `COPILOTO.md`. Cada etapa que se completa se marca acá en el
> MISMO commit; cada decisión se asienta; cada cosa descartada se borra con una
> línea de porqué. Si el doc no refleja el estado real, el trabajo está INCOMPLETO.
>
> Docs hermanos que hay que leer antes de tocar esto: `QUANTAI.md` (el programa de
> IA y sus reglas de oro — este agente es **P8**), `SALUD_CURVAS.md` (el catálogo de
> fallas que el agente aprende a reconocer), `RENTA_FIJA.md` §0 (los EJES y el
> motor), `VISTA_RESEARCH.md` §4.9/§4.10 (los números medidos de 1816 y el diseño
> previo, que este doc absorbe y reemplaza).

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

## Changelog

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
