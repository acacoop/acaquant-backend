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
| E2 | El simulador (TEA en seco) | no | no | pendiente |
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

## Changelog

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
