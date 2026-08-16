# AGENTE CURADOR — el primer agente de ACAquant  ⟨VIVO⟩

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

El **Curador** es un agente de **integridad de datos**: compara nuestra verdad
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
Curador tiene trabajo propio y lo hace de noche aunque nadie lo mire. Esa es toda
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
> escritura para el Curador y otro para la mesa: los dos pasan por `upsert_bono`.
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
| E1 | El espejo (detectar, sin escribir) | no | solo tabla propia | pendiente |
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

### E1 — El espejo (read-only, sin IA)
Un job que barre 1816 y contesta las tres preguntas **como reporte**. No escribe
en `mercado.curvas`, no llama a ningún LLM. Persiste solo en su tabla propia
(`rf_hallazgos`, con la evidencia congelada).

- Promover `censar()` del diag a módulo reusable — hoy vive en un `scripts/diag_*`
  y el job no puede depender de un script one-shot que la REGLA #5 manda borrar.
- Tres detectores deterministas: *falta en mi base* · *sin flujo* · *tasa
  sospechosa* (las reglas de `SALUD_CURVAS` §7.1: paridad fuera de [40,160],
  |TEA| fuera de rango, `moneda_flujo` ≠ CARTERA, escala flujo ≠ escala precio,
  bono en `curvas` sin espejo en `assets`).
- Respeta el throttle de 2,5 s del cliente y el tope de créditos (REGLA #4).

**Validación — y es la que decide si el proyecto sigue:** el user corre el job,
mira la lista, y marca qué es hallazgo real y qué es ruido. **Sin este número
medido, todo lo que viene después es fe.** El de las tasas sospechosas es el que
más importa: si de 20 candidatos 18 son ruido, las reglas están mal calibradas y
se ajustan ANTES de ponerle un modelo encima.

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
`evals/curador.json` con los casos que **hoy existen y tienen respuesta conocida**:

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
Tarea nueva en `core/ai.py`: **`curador_diagnostico`**, tier **pro** con
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

## Changelog

- **2026-08-16 — E0.** Nace el doc. Se define el encuadre (agente de integridad de
  datos, no copiloto), dónde va y dónde no va la IA, las 7 etapas, las 3 decisiones
  abiertas (alcance, política de conflicto, alcance del diagnóstico) y el inventario
  de lo reusable verificado contra el repo. Absorbe el diseño de
  `VISTA_RESEARCH.md` §4.10, que queda como el registro de la medición.
