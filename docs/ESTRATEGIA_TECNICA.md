# Estrategia técnica de TradingAV — las dos miradas, explicadas

> Documento vivo para entender las decisiones de arquitectura **a nivel gerencial
> y técnico**. Pensado para alguien de producto que opera el sistema solo y quiere
> aprender. No hay "bueno/malo" absoluto: hay **trade-offs** y **secuencia**.
> Última actualización: 2026-06-05.

---

## 0. El modelo mental (leer esto primero)

Cada decisión de arquitectura es una balanza entre **dos cosas que tiran para
lados opuestos**:

- **Capacidad / escala / potencia** (lo que el sistema puede hacer).
- **Complejidad / costo / fragilidad** (lo que cuesta construirlo, mantenerlo y
  que no se rompa).

La pregunta NO es *"¿es moderno?"* sino: **"¿la capacidad que me da justifica la
complejidad que agrega, EN MI ETAPA actual?"**

De ahí salen 3 principios para TradingAV:

1. **Nada se descarta — se secuencia.** Casi toda pieza "avanzada" se agrega
   cuando **dispara un gatillo medible** (ej. "cuando el endpoint X pase de 2s
   bajo carga real"). Antes del gatillo es **complejidad prematura** (pagás el
   costo sin cobrar el beneficio). Después es **necesaria**.
2. **La complejidad la absorbe la ingeniería (yo) + el sistema se autodefiende.**
   Que vos seas uno solo y no-dev NO veta tecnología potente; significa que yo
   tengo que construirla **bien, documentada y con guardrails**, para que no te
   quede algo que no podés sostener.
3. **Robusto-y-simple HOY y potente-MAÑANA no son enemigos: son una línea de
   tiempo.** Lo simple de hoy es la base sobre la que se monta lo potente de
   mañana, sin reescribir.

Las dos miradas que pediste —"algo sencillo y robusto" vs "que vuele / escale"—
son los **dos extremos de esa balanza**. Abajo, para cada decisión grande, te doy
las dos, el gatillo, y dónde estás vos hoy.

---

## 1. Sync vs Async (cómo el servidor maneja muchos pedidos a la vez)

**Qué es.** Cuando un usuario pide datos, el servidor habla con Mongo y espera la
respuesta. *Sync* = mientras espera, ese "carril" queda bloqueado. *Async* = el
carril se libera y atiende a otro mientras espera. FastAPI permite async, pero hoy
usás drivers **bloqueantes** (pymongo, requests) → es "async de nombre, sync de
verdad".

- 🟦 **Gerencial.** Async = más usuarios concurrentes con el mismo servidor (más
  barato por usuario). Sync = más simple de razonar y debuggear; con pocos
  usuarios rinde igual.
- 🟧 **Técnico.** Async real requiere drivers async (motor en vez de pymongo,
  httpx en vez de requests) y reescribir las rutas. Es contagioso (async se
  propaga). Mientras tanto, el atajo intermedio es un **thread pool** (lo que ya
  hago en el Diagnóstico): corrés varias queries en paralelo sin reescribir todo.
- ⏱️ **Gatillo.** Cuando, **medido**, las requests se encolen bajo carga real
  (CPU baja pero las respuestas tardan = están esperando I/O en fila).
- ✅ **Tu caso hoy.** 30 usuarios, picos en rueda. Sync alcanza de sobra. A 200,
  probablemente **siga alcanzando** con cache + rollups. Camino: primero thread
  pool donde duela; async completo solo si el gatillo dispara. **No ahora.**

---

## 2. Cache: en memoria del proceso vs compartido (Redis)

**Qué es.** Cache = guardar una respuesta ya calculada para no recalcularla.
Hoy usás `@cached` **en la memoria del proceso** de la API.

- 🟦 **Gerencial.** Redis (cache compartido) = todos los servidores/usuarios
  comparten el mismo cache → más rápido y consistente cuando crecés. Costo: un
  servicio más que mantener y monitorear.
- 🟧 **Técnico.** El cache in-process tiene 2 límites: (a) si corrés **varios
  workers** de uvicorn, cada uno tiene su cache → un dato cacheado en uno está
  frío en otro; (b) si escalás a **más de un servidor**, no comparten nada. Redis
  resuelve eso con un store central en RAM, y suma cosas (rate-limiting,
  locks distribuidos, colas).
- ⏱️ **Gatillo.** Cuando pases a **>1 worker/servidor** y la inconsistencia de
  cache se note (un usuario ve dato fresco, otro viejo), o cuando quieras rate-limit
  serio.
- ✅ **Tu caso hoy.** 1 proceso → el cache in-process es **correcto y suficiente**.
  Redis sería un servicio extra sin beneficio hoy. **Cuándo sí:** el día que
  pongamos varios workers para los 200 usuarios. Ese día es S de esfuerzo y vale.

---

## 3. Vistas materializadas (los "rollups"): a mano vs framework

**Qué es.** Precalcular un resumen pesado y guardarlo, para que la vista lea algo
chico en vez de escanear millones de docs. Ya lo hacés **5 veces** (OpsSerieDiaria,
ComercialCache, PnLTotalesCache, ConsolidadoCuentas, SnapshotsCierre).

- 🟦 **Gerencial.** Es la decisión de perf **más rentable** que ya tomaste: convierte
  vistas de segundos en milisegundos. El problema no es el patrón, es que está
  **copiado 5 veces** → 5 lugares para romperse y mantener.
- 🟧 **Técnico.** Cada rollup hoy = job propio + colección cache propia +
  live-fallback propio + cron propio. Eso es una **abstracción faltante**. Un
  equipo top tendría **un solo primitivo** "query materializada" (declarás:
  fuente, grano, cómo refrescar, fallback de hoy) y los 5 casos lo usan. Mongo
  además tiene `$merge` / *on-demand materialized views* nativas que podrían
  reemplazar parte del andamiaje manual.
- ⏱️ **Gatillo.** Ya disparó (van 5 copias). Es deuda que **conviene pagar pronto**
  porque cada nueva vista lenta hoy implica copiar la máquina otra vez.
- ✅ **Tu caso hoy.** **SÍ vale** unificarlo — pero como refactor seguro (esfuerzo
  M, riesgo bajo): un módulo `core/materialized.py`, migrar los 5 de a uno. Te
  saca código y te da un solo lugar confiable. Esto es de las cosas "potentes" que
  **sí** hacés ahora porque te **resta** complejidad, no te suma.

---

## 4. Topología de bases de datos (tu ejemplo: están desprolijas)

**Qué es.** Hoy hay **~14 bases de datos** Mongo. En Mongo una "base" es un límite
**pesado** (no podés joinear entre bases; cada una es un namespace aparte). El
estándar es **1 base / muchas colecciones**, o **pocas bases por dominio** con una
regla clara.

- 🟦 **Gerencial.** Datos desprolijos = (a) no sabés cuál es el número "verdadero"
  (el dólar está en 4 lugares), (b) cuesta agregar features (¿dónde guardo esto?),
  (c) **bloquea el futuro de ML / data-para-CFO** (no podés entrenar ni reportar
  sobre datos que no tienen un modelo claro). Es deuda que **frena el crecimiento**,
  no solo estética.
- 🟧 **Técnico.** Problemas concretos: el dólar fragmentado en `Trading.DOLAR` +
  `Valuaciones.{Dolar, DolarSnapshot, DolarOficialLive}`; `Operaciones` es **base
  Y colección** a la vez (colisión); snapshots live desparramados sin regla; toda
  una capa de **copias derivadas** (`*API` + `sync_api_copies`) que duplica
  escrituras y puede driftear. La cura: definir **dominios** (market-data /
  valuaciones / clientes / back-office / plataforma) y **una fuente de verdad por
  concepto**, con nombres sin colisión.
- ⏱️ **Gatillo.** El dolor ya está (tu queja #1 son los datos). Pero **mover Mongo
  es lo más riesgoso que hay** → carril rojo: medir, **dual-write** (escribir en
  viejo y nuevo en paralelo un tiempo), migrar lectura, recién borrar. Nunca de golpe.
- ✅ **Tu caso hoy.** **SÍ, es la fundación** — pero por fases y medido. Primero el
  mapa real (`diag_atlas_inventario`), después consolidar de a un concepto (el
  dólar es el candidato #1, bajo riesgo, alto valor didáctico).

---

## 5. Monolito vs microservicios

**Qué es.** Hoy es un **monolito**: una app FastAPI que hace todo (+ los motores
como procesos aparte). Microservicios = partir en servicios chicos independientes.

- 🟦 **Gerencial.** Microservicios = equipos grandes que trabajan en paralelo sin
  pisarse, y escalar partes por separado. Para **una persona**, son un **multiplicador
  de dolor** (más cosas que deployar, monitorear, y que fallan entre sí).
- 🟧 **Técnico.** El "monolito modular" bien hecho (módulos con límites claros) te
  da el 90% del beneficio sin el costo operativo. Tus motores ya son procesos
  aparte por una razón real (WS always-on) — eso está bien.
- ⏱️ **Gatillo.** Equipo de varias personas + necesidad de escalar una parte
  específica muchísimo más que el resto.
- ✅ **Tu caso hoy.** **Monolito, sí o sí.** Lo que sí hacemos es **modularizarlo**
  por dentro (partir los megafiles `operaciones.py`, `comercial.py`,
  `manager-view.tsx`) → mismo deploy, código más sano. Eso es "potente" y **resta**
  complejidad.

---

## 6. Capa de confianza de datos (tu dolor #1 — lo nuevo que propongo)

**Qué es.** Un subsistema explícito cuyo único trabajo es que **confíes en los
datos**: que no falten, que sean correctos, y que el sistema te **avise** cuando
algo está mal (porque vos no lo cazás a mano). Hoy NO existe como concepto.

- 🟦 **Gerencial.** Ataca de frente lo que te duele. Convierte "rezo que los datos
  estén bien" en "el sistema me garantiza, y si no, me avisa". Es lo que te deja
  dormir y lo que habilita usar estos datos para gerentes/CFO con confianza.
- 🟧 **Técnico.** Tres capas: (1) **contratos en la ingesta** — cada job valida lo
  que escribió (cantidad esperada, rangos, frescura) y **falla fuerte** si no
  cuadra (nada parcial en silencio); (2) **reconciliación automática** — cruces
  fuente-vs-base corriendo solos (como ya hacen Compliance y Sin-Operador), que
  muestran diferencias; (3) **SLAs de completitud visibles** — extender el árbol
  de Diagnóstico de "¿está vivo?" a "¿están **completos y correctos** los datos de
  hoy?". Más: **observabilidad** (JobRunLogger en todos los jobs — ya empezado) e
  **index/schema-as-code** (para que no vuelva a pasar lo del TTL puesto a mano en
  Atlas).
- ⏱️ **Gatillo.** Ya disparó — es tu prioridad declarada.
- ✅ **Tu caso hoy.** **Acá es donde más rinde invertir ahora.** Mezcla quick wins
  (observabilidad) con piezas nuevas (contratos, reconciliación). Bajo riesgo,
  altísimo valor para vos.

---

## 7. Plataforma de datos / ML (el futuro que querés)

**Qué es.** "El cerebro": que estos datos alimenten analítica avanzada y modelos
(predicción de churn de clientes, scoring, recomendaciones, etc.).

- 🟦 **Gerencial.** Es el upside grande (de "sistema operativo" a "ventaja
  competitiva"). Pero **se construye sobre datos limpios y confiables** — sin el
  punto 6 y el 4, ML es construir sobre arena.
- 🟧 **Técnico.** Cuando llegue: la pieza típica es separar la base **operativa**
  (la que usa la mesa, que tiene que ser rápida y estable) de una capa **analítica**
  (donde corrés queries pesadas y entrenás sin tocar producción). Puede ser tan
  simple como una colección/DB analítica alimentada por los rollups, o tan
  sofisticado como un warehouse — según el volumen.
- ⏱️ **Gatillo.** Datos confiables y modelados (puntos 4 y 6 hechos) + un caso de
  negocio concreto ("quiero predecir X").
- ✅ **Tu caso hoy.** **Todavía no se construye, pero se HABILITA ahora**: cada cosa
  del punto 6 y 4 es un ladrillo de esta fundación. No tirás nada; ordenás el
  terreno.

---

## 8. Síntesis: la secuencia integrada (las dos miradas juntas)

No es "simple O potente". Es **esta línea de tiempo**, donde lo simple de hoy
habilita lo potente de mañana:

| Fase | Qué | Mirada | Gatillo para la siguiente |
|---|---|---|---|
| **0. Estabilizar** | Cerrar incidente CPU (índice/TTL), JobRunLogger en todos los jobs | Robusto | Prod sin caídas + jobs observables |
| **1. Confianza de datos** | Contratos de ingesta + reconciliación + SLAs de completitud | Robusto **y** potente | Confiás en los números |
| **2. Fuente única de verdad** | Consolidar conceptos (dólar primero), unificar el primitivo de rollups | Potente, bajo riesgo | Modelo de datos limpio |
| **3. Modularizar** | Partir megafiles, ordenar bounded contexts de DBs (migración segura) | Mantenibilidad | Base lista para crecer |
| **4. Escalar (cuando dispare)** | Workers + Redis + async donde duela, medido | Potente | Solo si la carga lo pide |
| **5. Cerebro** | Capa analítica / ML sobre datos limpios | Upside | Caso de negocio + datos confiables |

**Vos estás entre Fase 0 y 1.** Lo "potente" (4, 5) no se descarta: se gana el
derecho a hacerlo habiendo hecho 1-3, y se dispara por necesidad medida, no por moda.

---

## Cómo seguimos

Yo soy tu ingeniería: pienso esto por vos, lo explico a los dos niveles, y cargo
la complejidad. Vos traés el producto y el negocio. Cuando proponga algo
"avanzado" va a ser con su gatillo y su porqué — para que entiendas y decidas, no
para impresionar. Este doc se actualiza a medida que avanzamos.
