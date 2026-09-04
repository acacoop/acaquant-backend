
# RENTA FIJA — mapa de datos (SQL)

> **Qué es este documento.** Mapa verificado **desde el código** (no desde otros
> docs) de la vista **RENTA FIJA** (`/renta-fija` en acaquant-web): qué datos
> muestra, de qué tabla SQL salen, quién las llena y cómo se relacionan.
>
> **Método.** Cada afirmación de abajo fue verificada leyendo el archivo y la
> línea reales (routers, services, motores, jobs, `sql/schema.sql`). Lo que **no**
> pude verificar está marcado explícitamente como `⚠️ a verificar` — no se asumió
> nada.
>
> **Estado: SQL-native (Mongo decomisionado 2026-06-29).** Toda la lectura y
> escritura de esta vista es Postgres/Supabase. Conexión vía
> `core.postgres.get_pool()`; escrituras SQL-native vía `core.pg_mirror`; lectura
> por dominio vía los services `*_sql.py` (`renta_fija_sql`, `curvas_sql`, etc.) y
> helpers (`core/market_snapshot`, `core/series_macro`). Ya no hay Mongo/Atlas.
>
> Fecha de relevamiento: **2026-06-12** (actualizado al decomiso de Mongo
> **2026-06-29**). Si cambia un motor/job/endpoint, este doc queda viejo —
> regenerar revisando las mismas fuentes.

---

## 1. Resumen ejecutivo

📋 **Qué es la vista:** una sola pantalla (`/renta-fija`) con **4 paneles** —
Tabla de bonos, Forwards, Curvas y Breakevens (+ sub-tab Libro). Es la pantalla
más pesada en datos de toda la sección MERCADOS.

📋 **De dónde sale todo:** **100% de SQL (Postgres/Supabase).** La vista lee ~16
tablas del schema `mercado` (+ macro y `valuaciones`). Se apoya en **3 tablas
base** (`mercado.curvas`, `mercado.market_snapshot`, `mercado.snapshots_cierre`)
y el resto **se deriva** de ellas (forwards, breakevens, fair value son
**cálculos**, no datos crudos).

📋 **Arquitectura de datos (lo importante):**
- **Lectura: SQL-native.** Todos los endpoints de renta fija leen de Postgres vía
  `core.postgres.get_pool()`, a través de los services `*_sql.py` y helpers
  (`core/market_snapshot`, `core/series_macro`).
- **Escritura:** los motores y jobs escriben SQL-native vía `core.pg_mirror`.
  El live de forwards/breakevens (la matriz intradía) se persiste en sus tablas
  `mercado` y su cierre diario en las tablas de histórico correspondientes.

## 0. REDISEÑO EN CURSO (2026-08-15) — leer antes de tocar la vista

Decisión del user: **la vista `/renta-fija` se reformula entera**. Está lenta y
la clasificación de curvas quedó vieja. Se hace **por pasos**, y los dos
primeros son invisibles para la pantalla a propósito: desactivan el riesgo antes
de tocar la vista más usada de la app.

| # | Paso | ¿Toca la vista? | Estado |
|---|---|---|---|
| 1 | Medir por qué tarda | no | ✅ **medido** (el diag ya cumplió y se borró) |
| 2 | Clasificar los 222 con los ejes (`scripts/clasificar_curvas.py`) | no | ✅ **aplicado** (212/222, test VERDE) |
| 3a | Endpoint `GET /api/cotizaciones/curvas-vista` (nadie lo consume) | no | **hecho** |
| 3b | Tab **CURVAS** en el front (ARS izq / USD der) + absorber ONs | sí | ✅ **hecho** |
| 4 | Tab **FORWARDS** (+ Fair Value adentro) | sí | pendiente |
| 5 | Job de 1816 → altas automáticas (`docs/RESEARCH.md` §4.10) | no | pendiente |
| 6 | Renombrar las columnas de `mercado.curvas` | no | ✅ **hecho** |
| 7 | Migrar el blob `data` (y matarlo) + ficha única en `assets` | no | pendiente |
| 8 | `mercado.especies` — las PATAS de cada bono | no | ✅ **aplicado** (758 patas) |
| 9 | Limpiar el VALOR de `curvas.ticker` (sacar el sufijo D/C) | sí | pendiente |
| 20 | Perf con EMISOR=CORPORATIVO + filtro de TEA + **ficha del bono** (§20) | sí | ✅ **hecho** (2026-08-28) |
| 21 | Modal **SIMULAR INVERSIÓN** (importe + bono + precio → TIR y cronograma) (§21) | sí | ✅ **hecho** (2026-08-30) |
| 24 | Layout de los dos modales de la vista: 50/50 en la FICHA DEL BONO, y la ficha sale del simulador (§24) | sí | ✅ **hecho** (2026-09-03) |
| 25 | Filtro de **EMISOR por nombre** en la tab CURVAS (§25) | sí | ✅ **hecho** (2026-09-04) |

### Paso 25 (2026-09-04) — el filtro de EMISOR por NOMBRE (y la fila pasa a llamarse TIPO)

**Qué faltaba.** Con `TIPO=CORPORATIVO` la tabla son ~134 ONs de decenas de
emisores, y los dos controles que había —el tipo de emisor y el piso de TEA— no
contestan la pregunta que la mesa se hace ahí: *qué tiene YPF, y a cuánto rinde
contra Pampa*. El piso de tasa recorta el largo de la lista, no la ordena por
quién emite. Es el mismo problema que resolvió el `TEA ≥` del paso 20 (una lista
de 134 filas no se lee), atacado por el otro eje.

**Qué se agregó.** Un multi-select con buscador a la izquierda del `TEA ≥`. Corre
en la MISMA cadena: `TIPO → EMISOR → TEA`, un solo `useMemo` alimentando la
tabla, el gráfico, los contadores de las pills y el universo del LIBRO. Ese
encadenamiento es el punto — si el filtro tuviera su propia lista, la pantalla
podría contradecir a sus propios controles.

**Las cuatro decisiones:**

1. **La CLAVE de agrupación la manda el backend, no el navegador** (REGLA #9).
   `curvas-vista` suma `emisor_key` = `upper(btrim(emisor))`, que es *la misma*
   regla con la que la base joinea el emisor con su industria. Si el front
   agrupara por el string suelto, `'YPF '` y `'YPF'` serían **dos chips**, cada
   uno contando bien por su cuenta, y el que filtra por uno ve la mitad de los
   bonos de YPF. No falla nada: simplemente faltan. El nombre (`emisor`) sigue
   viajando aparte porque es lo que se MUESTRA — nombre e identidad son dos
   cosas distintas y por eso van en dos campos.

   > 1816 estandarizó los nombres de los corporativos (`jobs/ficha_1816`), así
   > que hoy la clave probablemente no cambie nada. Eso es exactamente por qué
   > se pone ahora: el día que entre un emisor a mano —los provinciales se
   > cargan así— el filtro no se parte en dos, y nadie tiene que acordarse.

2. **El catálogo es lo que el TIPO ya dejó pasar**, no el universo. Ofrecer 67
   emisores mientras se miran soberanos es ruido, y los contadores dirían un
   número que la tabla no muestra. Por lo mismo, al cambiar el TIPO se **sueltan**
   los emisores tildados que ese TIPO ya no muestra: sacar CORPORATIVO dejaba
   `EMISOR · YPF` encendido sobre una tabla de soberanos —cero filas y ningún
   control que lo explicara—.

3. **Vacío = TODOS**, al revés que el filtro de TIPO (donde "ninguno tildado"
   mentía mostrando todo). Acá no es ambiguo porque **el botón dice el estado
   sin abrirlo**: `EMISOR · YPF` con uno, `EMISOR · 3` con varios. Es la misma
   mitigación que el `TEA ≥ 10%`.

4. **Cuenta BONOS, no filas.** Un dual llega REPETIDO (una fila por pata), así
   que el chip diría 4 donde hay 3 — el mismo cuidado que el backend ya tiene
   con el contador por tipo de emisor.

**Los bonos SIN emisor cargado no se esconden**: son su propio grupo,
`(SIN EMISOR)`. Un bono que desaparece de una lista no se nota, y "nadie lo
cargó" es un estado, no un error.

**Los dos deploys NO tienen que ser simultáneos.** El front va a Vercel solo y
el backend se sube a mano, siempre después: mientras `emisor_key` no llegue, la
tab la deriva del nombre con la misma regla (`upper` + espacios colapsados) y en
cuanto el campo aparece manda el backend. Sin esa caída, entre un deploy y el
otro **todos** los bonos habrían caído en `(SIN EMISOR)` — un filtro con una
sola opción, roto sin decirlo.

⚠️ **La fila de pills pasó a llamarse TIPO.** Eran SOBERANO / PROVINCIAL /
CORPORATIVO / BCRA bajo el rótulo EMISOR — que es el tipo de emisor
(`emisor_tipo`), no el emisor. Con los dos controles en la misma barra, dos cosas
llamadas EMISOR es cómo se termina filtrando por una creyendo que se filtró por
la otra.

**No se persiste**, por lo mismo que el piso de TEA: un filtro que esconde bonos
y sobrevive a la navegación es una pantalla que le miente al que vuelve a ella.

### Paso 24 (2026-09-03) — los dos modales: cada dato en UN solo lugar y a la vista

Cambio de LAYOUT, cero cálculo: no se tocó un endpoint, un service ni una
fórmula. Las dos partes salen del mismo problema — **el mismo dato repetido, y
el dato que importa fuera de pantalla.**

**(a) La FICHA sale del modal SIMULAR INVERSIÓN.** El simulador cerraba con un
panel `FICHA · <ticker>` idéntico al que ya muestra la FICHA DEL BONO. Un dato
en dos pantallas es la REGLA #9 en versión chica: mientras coincidan no pasa
nada, y el día que una se actualice sola nadie sabe cuál manda. Acá además no
compraba nada — el simulador contesta *cuánto rinde y cuándo cobro*, y el emisor
o la ley del papel no entran en esa cuenta. Se fue el panel y con él el armado
de filas; el resto del modal quedó igual.

**(b) La FICHA DEL BONO pasa a 50/50.** Los cuatro bloques iban apilados a todo
el ancho —tasas, gráfico, cronograma, ficha— así que para leer la ficha había
que scrollear, y al scrollear se perdía de vista el cronograma: **las dos cosas
que se comparan nunca estaban juntas en pantalla**. Ahora las TASAS quedan
arriba a todo el ancho (es el titular) y abajo el cuerpo se parte al medio: a la
izquierda la FICHA en filas verticales label→valor ocupando toda la altura, con
el rótulo escrito en vertical sobre el lomo; a la derecha el gráfico arriba y el
cronograma abajo. Cada mitad scrollea por dentro, así el modal entero no se
mueve. Debajo de `lg` se apila como antes (la ficha al final) y scrollea el
modal.

**(c) La cabecera comprimida y el flujo en DOS escalas** (misma entrega, segunda
pasada). Dos cosas que sólo se ven con el modal ya armado:

- **TASAS Y RIESGO pasa de cuadro a TIRA de una línea** (rótulo y valor inline en
  vez de apilados). Medido: **de ~100 px a 30 px**. En un modal el alto es lo
  único que no sobra — cada píxel de la cabecera se lo saca al cronograma, que es
  el dato que se mira. La FICHA además se lleva **37% del ancho** (era 50): sus
  filas son cortas y el que necesita ancho es el gráfico.
- **El FLUJO pasa a DOS escalas** (capital arriba, renta abajo, mismo eje de
  fechas) — el criterio que ya usaba SIMULAR INVERSIÓN, ahora también acá.
  Apilado en una sola escala, en un bullet como AO28 la amortización (100)
  aplasta al cupón (0,50): la renta se dibujaba pegada al cero y **el bono
  parecía no pagar nada hasta el vencimiento**. El doble eje Y no era la salida
  (misma unidad en dos escalas: el ojo compara alturas que no son comparables).

- **Archivos**: `bono-modal.tsx` y `simular-inversion-modal.tsx` en
  `acaquant-frontend`. Nada en el backend.

### Paso 23 (2026-09-02) — la TNA de TASA FIJA pasa a la convención de 1816

**El veredicto de la auditoría, con los 11 bonos de la pill medidos en prod:**

| Qué se comparó | Resultado |
|---|---|
| lo guardado vs. recalcular con la función del motor | **0 desfasados** de 11 |
| precio nuestro vs. el de 1816 (`precioDirty`) | idéntico en **10 de 11** (el que no, 0,05%) |
| **TEA** nuestra vs. la de 1816 | **≤0,02 pp** en los 11 |
| duration nuestra vs. la de 1816 | **0** con diferencia >0,05 |
| **TNA** nuestra vs. la de 1816 | **hasta 2,59 pp**, creciendo ordenado por plazo |

O sea: **no había nada mal valuado**. El motor arranca del mismo precio y llega a
la misma TEA y a la misma duration que el proveedor contra el que valida la mesa.
Lo único que difería era la CONVENCIÓN de una columna — que es la clase de error
que no falla: un número plausible, en el lugar correcto, calculado con otra
fórmula que la del que lo lee.

**Qué convención.** 1816 lo DECLARA: el campo `convencionTna` (estaba en el enum
del spec y no se pedía) devuelve **`plazo-rem`** en 11 de 11. Y no hace falta
creerle: reconstruida desde su propia `tea`, la lineal base 365 reproduce su
`tna` con **error 0,00 pp** en los 11, mientras que TEM×12 no le pega a ninguno
(hasta 2,59 pp) y la efectiva tampoco (hasta 2,86 pp).

    TNA = ((1 + TEA)^(dias/365) − 1) × 365/dias      ← `quant.tasas.tna_plazo_remanente`

donde `dias` va de la **liquidación** (T+1 hábil) al vencimiento.

**La firma de que es convención y no bug**: la diferencia crece ORDENADA con el
plazo — 0,14 pp a 12 días, 2,59 pp a 300. Un error de valuación daría
diferencias desordenadas.

**Alcance, y por qué es tan chico.**

- **Solo la pill `tasa_fija`.** Las letras son BULLET: un pago al final, así que
  «plazo remanente» ES el plazo de la plata. En un amortizante el vencimiento
  final lo sobreestima y qué hace 1816 ahí **no está medido**. Para medirlo:
  `python -m scripts.diag_tasa_fija --pill cer`.
- **TAMAR y BADLAR no cambian NADA** (pedido explícito del user). TAMAR conserva
  la TNA de 1816 donde la hay y la derivada donde no; BADLAR no llega a esta
  tabla (`curvas_ejes` no le da pill). Congelado por
  `test_TAMAR_sin_1816_NO_cambia_de_convencion`.
- **Donde 1816 manda su propia TNA no se pisa**: ya viene en su convención.

**Dos cosas de diseño que salieron de acá:**

1. **La calcula el BACKEND.** El front la derivaba con `Math.pow` — ninguna
   pantalla deriva números, o mañana hay dos TNAs para el mismo bono según quién
   la calculó. `bonos-table.tsx` ya prefería `metrics.TNA` cuando venía, así que
   **no hizo falta deploy simultáneo del front**: el día que llega el campo, la
   columna cambia sola.
2. **Cada fila dice en qué convención está** (`tna_convencion`: `plazo-rem` ·
   `1816` · `mensual`). Mientras convivan dos convenciones en la misma pantalla,
   esa es la única forma de que la diferencia no sea silenciosa — que es
   exactamente cómo vivió meses.

**Lo que NO se resolvió**: por qué la planilla de la mesa muestra bajo el rótulo
TNA un número igual a la TIR (T30A7: 28,89% = su propia TIR, contra 27,64% de la
lineal con los mismos 239 días y el mismo precio 133,25). Con la TNA en
`plazo-rem` **los dos números quedan en la misma fila** — el de la planilla es la
columna TEA de al lado — así que la pantalla ya no obliga a elegir.

### Paso 22 (2026-09-02) — auditoría de la TNA: qué se arregló y qué falta medir

Disparador: *«veo bonos con una TNA que se rompió, y en RENTA FIJA la veo SIEMPRE
IGUAL»*. La auditoría separó dos cosas que en pantalla se ven idénticas.

**(a) ARREGLADO — la fila mezclaba dos cálculos.** `curvas_vista._armar` limpia
las métricas del motor cuando manda 1816 o cuando la fila es la pata SECUNDARIA,
y su propio comentario dice «se van TODAS las derivadas». No era cierto:
`duration` y `paridad` estaban fuera de la lista. No se notaba porque el renglón
siguiente las repone… **solo si 1816 las trae**, y el job cuenta 8 de los 9
corporativos con pata TAMAR como `sin_dato`. Ahí la fila salía con la TEA de 1816
al lado de la DURATION y la PARIDAD del cálculo que esa misma rama acababa de
declarar basura — reproducido: TMF27 con TNA 32,8% (1816) y DUR 0,53 / PARIDAD
99,0 heredadas de su TEA vieja de −25,0%.

No es cosmético por dos motivos: `tasa_ruido` **se decide por duration**, así que
una duration ajena podía apagar (o dejar prendida) una tasa que no le
corresponde; y el gráfico de curvas usa la duration como eje X (`curvas-chart`
descarta `duration == null`), o sea que esos bonos estaban **graficados en la
abscisa equivocada**. Con el fix la celda queda vacía y el punto sale del
gráfico, que es la misma respuesta que ya se había elegido para la TEA. Congelado
por `test_si_1816_NO_trae_duration_la_del_motor_NO_se_queda`.

**(b) A MEDIR — «siempre igual» no es un bug hasta que se mida.** El motor
recalcula **solo cuando cambia el `last_price`** (cache `ultimo_calculado` en
`engines/curvas.py`), `core.market_snapshot.last_prices` **no filtra por
frescura** (trae cualquier fila con `last_price > 0`, sea de hoy o de hace
meses), y la vista **no manda la fecha de la tasa del motor** (`tea_fecha` viaja
solo para 1816). Las tres juntas hacen que una TNA de hace tres semanas y una de
hace tres segundos se dibujen exactamente igual. Cuánto de la tabla está en ese
estado **no está medido**: lo mide `python -m scripts.diag_tna` (serie de
`snapshots_cierre_hist`: ruedas sin movimiento, saltos con su fecha, y hace
cuánto que cada número no se recalcula). Sin ese número no se toca el motor.

### Paso 21 (2026-08-30) — el modal SIMULAR INVERSIÓN (y la baja de la vista ESTRATEGIA)

**La pregunta que contesta**: *"si pongo $X en este bono a este precio, ¿qué tasa
me llevo y cuánta plata me entra, cuándo?"*. La tabla da la TEA al last; el modal
deja **tocar el precio** y ver la TIR responder — que es como se piensa una orden
que no va a pegar al last.

**El contrato que ordena todo**: la simulación corre **EL MISMO MOTOR** que la
tabla. `engines/curvas.py::calcular_campos` recibe el precio como argumento (no
lo busca), así que simular es llamarlo con el precio tipeado — el patrón que ya
usaban `agente/alta._simular_tasa` y `jobs/backfill_tasas`. El cronograma sale de
`bono_detalle.cronograma` (despacho por rama — sumar `amortizacion + interes` a
mano daría CERO en soberanos y CER) y las conversiones TEM/TNA de `quant.tasas`.

- **Backend**: `api/services/simular_inversion.py` + `GET /api/analitica/simular-inversion`
  (`ticker`, `importe`, `precio` opcional — default el last, que viaja como
  `precio_referencia`). Escala: `vn = importe × 100 / precio`; la moneda del
  PRECIO la decide el sufijo del símbolo (regla de `precio_soberano_a_usd`, NO
  `moneda_eje`: AL30 tiene eje USD pero su pata en pesos cotiza en ARS). CER se
  ajusta por CER de liquidación por flujo; sin CER publicado se proyecta con el
  último constante y viaja `cer_proyectado=true` (a diferencia de la FICHA, que
  muestra los valores contractuales de emisión: la pregunta de este modal es
  cuánta plata entra, no qué promete el papel). Tests: `tests/unit/test_simular_inversion.py`
  (el motor real corre en el test, no un mock).
- **Frontend**: botón SIMULAR INVERSIÓN en la barra de tabs de `/renta-fija`
  (`renta-fija-live.tsx`) → modal `simular-inversion-modal.tsx`. El universo del
  selector son los tickers de `curvas-vista` (dedupe por `ticker_corto`).
  Izquierda: resultado al precio simulado + la compra + ficha. Derecha: flujo de
  fondos apilado (amortización/interés) + cronograma, escalados al importe.
  Fetch con debounce de 400 ms; los importes con `NumeroInput` (es-AR).
- **En la misma entrega se dio de baja la vista ESTRATEGIA (`/retorno`)**:
  COMPARAR INVERSIÓN y DESCOMPOSICIÓN se borraron (endpoints, services, front),
  ANÁLISIS SENSIBILIDAD se mudó tal cual a RESEARCH y el módulo `estrategia`
  salió de `core/roles.py`. Lo que COMPARAR contestaba con dos bonos, este modal
  lo contesta con uno y precio editable.

### Paso 20 (2026-08-28) — la vista con EMISOR=CORPORATIVO, el filtro de TEA y la FICHA del bono

Tres cosas en una entrega, y la primera explica por qué las otras dos hacían falta.

#### 20.a — Por qué CORPORATIVO ponía la pantalla lenta

**La causa es 100% del navegador.** El filtro de EMISOR es client-side (el
`emisor_tipo` viaja en cada bono), así que elegir CORPORATIVO **no le pega al
backend**: el payload de `curvas-vista` es byte por byte el mismo y su cache TTL
de 10 s tampoco cambia. Lo que cambia es cuánto tiene que dibujar recharts, y
cambia de una forma que se multiplica sola.

En el gráfico, cada **familia** es una serie propia: una `<Line>` con su fit y un
`<Scatter>` con sus puntos. Para un soberano la familia es la LEY (Bonares /
Globales) → **2**. Para un corporativo es la **INDUSTRIA** del emisor → hasta
**10**. Y cada familia generaba su fit como una rampa de **101 puntos propios**.

recharts recibe **un solo dataset** con una fila por valor de X, así que esas
rampas no se comparten: cada una aporta 101 filas que ninguna otra serie usa. Y
después **cada serie recorre el dataset entero**.

| | familias | series | filas del dataset | recorridos |
|---|---:|---:|---:|---:|
| EMISOR = SOBERANO (69 bonos) | 2 | 4 | ~270 | ~1.100 |
| EMISOR = CORPORATIVO (134) | 10 | 20 | ~1.150 | **~23.000** |

**~20× más trabajo, cada 5 segundos** (la tabla es live) y **en las dos
columnas**. Encima cada `<Scatter>` lleva un `<LabelList>`, y recharts evalúa una
etiqueta **por fila del dataset y por serie**: ~11.000 etiquetas para dibujar 102
tickers que, a 10 px, se pisan entre ellos y no se leen.

**Los dos arreglos:**

1. **La grilla del fit es COMPARTIDA** (`GRID_FIT = 48` para todo el gráfico, no
   101 por familia). Las 10 industrias comparten las mismas filas → el dataset
   baja de ~1.150 a ~150 **sin que el dibujo cambie**: una curva log sobre 48
   puntos ya es suave, y los 101 originales caían varios dentro del mismo pixel.
   Cada familia se evalúa **solo dentro de su propio rango** —un fit no se
   extrapola fuera de los bonos que lo generaron— y como ese rango es contiguo,
   los huecos quedan en las puntas: el `connectNulls` de la Line no puede
   inventar un tramo.
2. **Las etiquetas de ticker se apagan solas** arriba de 40 puntos, con un botón
   `TICKERS` que dice en qué estado están y deja forzarlo. Apagar algo en
   silencio es peor que la lentitud: el botón existe para que no sea un misterio.

⚠️ **Lo que NO se tocó y por qué.** No se movió el poll de 5 s ni se metió el
filtro de emisor en el backend: el problema no era la red ni la base, y "arreglar"
lo que no está roto habría cambiado la frescura de la tabla —que es lo que la
mesa mira— para no ganar nada.

#### 20.b — Filtro de TEA (`TEA ≥`)

Un piso de tasa, a la **derecha de todo** (desde el paso 25 lo precede el filtro de
EMISOR por nombre; la fila de pills se llama TIPO). Corta sobre lo que el
emisor ya dejó pasar, y ese orden se lee de izquierda a derecha. Aplica a **la
tabla, el gráfico, los contadores de las pills y el universo del LIBRO** desde una
sola fuente (el `useMemo` de `bonos`), así la pantalla no puede contradecir a sus
propios controles.

⚠️ **Es GLOBAL y las dos escalas NO son comparables.** En ARS las TEA viven entre
30 % y 60 %; en USD, entre 5 % y 15 %. El mismo `TEA ≥ 5` no filtra **nada** a la
izquierda y sí a la derecha. Se planteó hacerlo **por columna** y el user eligió
uno solo, arriba, como el de EMISOR — decisión tomada a conciencia. La mitigación
es que el botón **muestra el número activo** (`TEA ≥ 10%`) en vez de guardárselo:
el efecto asimétrico tiene que ser visible, no una sorpresa cuando una columna se
vacía.

**Un bono sin TEA no puede cumplir "TEA ≥ 5", así que sale — pero eso NO es lo
mismo que no llegar al piso**, y la pantalla no puede tapar la diferencia: se
cuentan aparte y el botón los muestra (`−7`). La **tasa ruido** entra en la misma
bolsa: con duration ~0 el número existe pero es un artefacto de anualizar pocos
días (una ON a 3 días marcaba 142 %), así que un piso de tasa la dejaría pasar
**siempre y arriba de todo** — justo al revés de para qué sirve el filtro. Es el
mismo criterio con el que el gráfico ya la excluye, y lo decide el backend
(`tasa_ruido`), no el navegador.

No se persiste: un filtro que esconde bonos y sobrevive a la navegación es una
pantalla que le miente al que vuelve a ella.

#### 20.c — La FICHA del bono (`GET /api/cotizaciones/bono/{ticker}`)

Click en cualquier fila abre un modal con el **flujo de fondos** (barras apiladas
amortización + interés, los pagos vencidos apagados), el **cronograma en tabla**,
el **bloque de tasas y riesgo por pata** y la **ficha** del papel. Contesta la
pregunta que sigue siempre a mirar la tabla —*¿y cuándo paga?*— y que no estaba en
ninguna pantalla de mercado: vivía dentro de `/api/titulos/flujos`, que devuelve
los 222 bonos con su cronograma completo (**240 KB para mirar uno**).

⚠️⚠️ **La regla que sostiene todo el service: el cronograma sale de la MISMA
función que usa el motor para la TEA que muestra la fila.** El master **no guarda
los flujos con un shape único** — guarda tres, y cuál toca lo decide
`engines.curvas.rama_calculo` a partir de los EJES:

| rama | fórmula | campos |
|---|---|---|
| `soberanos` / `dolar_linked` | `monto_flujo_soberano` | `amortizacion_pct` + `cupon_sobre_residual` (**ya resuelto en moneda**) |
| `cer` | `monto_flujo_cer` | `amortizacion_pct` + `cupon_sobre_residual` × `residual_previo_pct` (**acá SÍ es una tasa**) |
| `on` / `tasa_fija` / resto | `monto_flujo` | `amortizacion` + `interes` (absolutos por 100 VN) |

Sumar `amortizacion + interes` para todos —que es lo que uno escribe sin mirar—
daría **CERO** en soberanos y en CER: sus campos se llaman distinto. No tira, no
avisa, **dibuja un gráfico vacío que parece un dato**, al lado de una TEA del
12 %. Por eso el service no elige la fórmula: se la pregunta a `rama_calculo`.

Y `cupon_sobre_residual` **significa dos cosas distintas según la rama** (una tasa
en CER, un monto ya multiplicado en soberanos) con el mismo nombre y sin ninguna
validación que las separe. Congelado por `tests/unit/test_bono_detalle.py`.

**El BULLET no tiene array de flujos.** Las Lecaps/Boncaps pagan todo al
vencimiento y el master lo guarda en `flujo_vencimiento`, no en `flujos`. Sin ese
caso, **media pill TASA FIJA —la que más se mira— abriría el modal vacío**, y
"sin cronograma cargado" se vería igual que "paga todo al final".

**Lo que el modal NO hace: no calcula tasas.** TEA, duration, paridad y margen
salen de `curvas_vista`, o sea de la misma consulta que dibuja la fila que lo
abrió. Recalcularlas sería abrir la puerta a que el modal y la tabla muestren dos
números distintos para el mismo bono.

### Pasos 10-13 (2026-08-16) — duales, ejes editables, industria y el PnL

Cuatro cambios y **cuatro bugs que no se veían**. El patrón se repite tanto que
conviene nombrarlo: *ninguno de los cuatro producía un error, una fila faltante ni
un número raro. Los cuatro seguían sumando bien.*

#### Paso 10 — un DUAL tiene dos patas, no una familia propia

`ajuste='dual'` estaba mal por dos motivos y el segundo es el grave: sacaba al
bono de las dos tablas donde el trader lo busca, y **destruía el dato** (no decía
contra qué ajusta, así que se perdía que uno es CER+TAMAR y otro CER+devaluación).

Modelo: **`ajuste` + `ajuste_alt`**. Con eso "es dual" deja de cargarse y se
deduce (`ajuste_alt IS NOT NULL`), las tablas salen sin reglas especiales
(`CER = ajuste='cer' OR ajuste_alt='cer'`) y **la pill DUALES se eliminó**.
`ce.pill()` pasó a `ce.pills()`, que devuelve una TUPLA; `curvas_vista` emite
**una fila por pill** — repetido y no como lista, para que el contrato del front
no cambie y los dos deploys no tengan que ser simultáneos.

De dónde salió cada pata: la 1ª de `curva` (donde la mesa lo archivó), la 2ª de
`data->>'tasa_referencia'`. Los 3 que ninguna fuente tenía los dijo la mesa
(`PATAS_MANUALES`): TTD26 y TTS26 son TAMAR+FIJA, TMVE8 es TAMAR+DOLAR LINKED.

> ⚠️ **CORRECCIÓN (2026-08-16).** Acá decía «1816 no sirve y está medido»,
> apoyado en que la ficha de los duales trae 9 campos y la denominación de los
> ocho es `GOB ARS ARG DUAL (<ticker>)`. **La medición era buena y la conclusión
> era falsa**: se le había preguntado por el ticker PELADO. 1816 publica cada
> pata como un **TICKER APARTE** (`TXMD9 @CER`, `TXMD9 @TAMAR`) con su propio
> cashflow y su propia tasa. La ficha del pelado no las nombra porque las patas
> no son campos suyos: son instrumentos. Ver **Paso 18**.



⚠️ **TMVE8 es el primer bono que CRUZA DE COLUMNA** (TAMAR es ARS, DOLAR LINKED es
USD). Eso destapó que el front decidía el lado de la tabla con
`bonos[0].moneda` — con TMVE8 primero, la tabla USD entera mostraba columnas de
pesos. Ahora decide por `lado`, que el backend manda por fila.

⚠️ **`dual` salió de `AJUSTES` y las dos curvas "Duales" de 1816 salieron de
`EJES_1816`** (viven en `CURVAS_SIN_EJES` con su motivo). Si no,
`clasificar_curvas --aplicar` le devolvía `ajuste='dual'` a cada dual ya migrado:
el pipeline de 1816 peleando contra el modelo, en silencio y para siempre.

#### Paso 11 — los EJES se pueden editar (y el bug que borraba el emisor)

Los 5 ejes solo los escribía un script one-shot, así que **un bono dado de alta
desde Manager nacía sin clasificar y no aparecía en la vista**. Ya pasaba con cada
alta. Ahora se editan en Manager → TÍTULOS → BONOS, sección CLASIFICACIÓN.

Los ejes van **solo a las COLUMNAS, nunca al blob `data`**: meterlos ahí sería
agrandar el problema de las dos verdades justo cuando se está cerrando.

**Bug encontrado leyendo el camino de escritura, no reportado por nadie:**
`ficha_1816` escribe el emisor en la COLUMNA y el editor mergea sobre el BLOB, que
nunca se enteró. Editar cualquier bono escribía `emisor = NULL` (bonos no-ON) o
revertía a la grafía vieja (ONs). `fila_a_escribir` ahora saca del dict las
columnas que el blob ya no gobierna.

**Y un bug que el paso mismo iba a introducir:** `list_bonos` lee el blob y los
ejes son column-only → el form los cargaba vacíos y guardar los borraba. Ahora
`list_bonos` mergea las columnas encima, y la columna SIEMPRE le gana.

`extra="forbid"` en los upserts: con el default `ignore`, un campo que el backend
viejo no conoce se descarta con **200 OK** — y el front deploya a Vercel solo y
siempre antes. Sin esto, en esa ventana el operador clasifica, ve el tilde verde,
y el bono sigue sin clasificar.

#### Paso 12 — la INDUSTRIA se muda del bono al EMISOR

Medido: **8 de 51 emisores corporativos tienen sectores que se contradicen entre
sus propios bonos** (Pampa Energía tiene tres). Ninguna fila está mal — cada una
suma bien por separado — y por eso agrupar da distinto según de dónde se lea.

`mercado.emisores` (PK de TEXTO + índice único sobre `upper(btrim(...))`) +
`mercado.industrias` (catálogo controlado, patrón `mercado.rubros`). ABM en
Manager → TÍTULOS → EMISORES, con los pendientes ARRIBA. La industria se resuelve
en la LECTURA (`curvas_vista` la joinea) y **solo viaja para corporativos**.

El seeder **NO resuelve las contradicciones**: colapsarlas por mayoría sería
inventar un criterio y dejarlo escrito como si fuera un dato. `on_otros` tampoco
se traduce a una industria "otros" — *otros* no es una industria, es el cajón de
lo que nadie clasificó. En el gráfico, `sin_industria` tiene **color y etiqueta
propios**: mezclarlo con `otros` haría que el pendiente desaparezca justo cuando
el trabajo de cargarlo está a medias.

Guardrail nuevo: `emisor_sin_industria` (respeta umbral) y
`emisor_contradictorio` (**siempre viola** — no hay cantidad tolerable de "el
mismo emisor dice dos industrias distintas").

#### Paso 13 — precios congelados en el fallback del PnL

`mercado.snapshots_cierre` es el fallback de precio del PnL, se poblaba barriendo
3 de las 7 familias y **nunca borra** (su upsert solo AVANZA la fecha). Un bono
que dejó de entrar al barrido se quedaba con el último precio **para siempre**.

Medido: 5 bonos VIVOS y EN CARTERA con el precio del 30-abr, desviados 5,8%-9,6%
— TTS26 (17 cuentas, 5.153 M de nominales), TTD26, D30S6, TZV27, TZV28. Los cinco
viven en `tamar` o `dolar_linked`, las dos familias que faltaban en `CURVAS_V1`.

SALUD no lo veía: su contrato mira `max(fecha)` de la TABLA, que sigue fresco
mientras cualquier otro ticker actualice. **La frescura de una fila no es la
frescura de la tabla.**

Las 155 ONs siguen sin cierre persistido — decisión aparte, por volumen.

#### Fase B: `sql_universo`, y por qué no avanzó más

`core.curvas_ejes.sql_universo(curva, fit=)` traduce cada curva a un predicado
sobre los ejes. **Son DOS y no uno:**

  · **VISTA** — qué se muestra. Junta emisores a propósito.
  · **FIT** — qué entra al ajuste de la curva. Acá juntar emisores está MAL: un
    corporativo tiene spread de crédito y corre el fit para todos, y el modo de
    fallar es mudo.

Medido: con el predicado de VISTA `soberanos` pasa de 21 a 129 bonos. Con el de
FIT, el universo de hoy se reproduce **bono por bono** en las tres curvas que
tienen fit persistido (tasa_fija 11=11, cer 22=22, dolar_linked 7=7).

⚠️ **Lo que falta y por qué es delicado:** `jobs/fair_value.py` NO lee
`mercado.curvas` — lee `snapshots_cierre_hist WHERE curva = X`, o sea que su
universo lo define la CLAVE PERSISTIDA de una tabla con 4.896 filas de historia
(y ninguna de las 4 tablas particionadas por `curva` se limpia nunca: no hay un
solo `DELETE FROM` sobre ellas en el repo). Migrarlo no es cambiar un filtro: es
tocar una PK con historia, el único paso irreversible del plan. La salida
propuesta es que la clave pase a DERIVARSE de los ejes conservando el mismo
string — así ningún consumidor cambia y no hay historia que migrar.

**Decisión de mesa pendiente:** si un dual entra al FIT de sus dos curvas. Cotiza
distinto que un CER puro porque tiene la opción de la otra pata. Hoy `sql_universo`
los incluye; sacarlos es quitar el `OR ajuste_alt` del modo `fit`.

#### Paso 14 (2026-08-16) — `por_curva` deja de leer la columna `curva`

`core/curvas_sql.py::por_curva(curva)` es el helper que **~20 archivos** usan para
preguntar "dame los bonos de esta curva": forwards, breakevens, fair value,
sintéticos, carry, sensibilidad, order book, el healthcheck.
Hasta hoy contestaba comparando contra `mercado.curvas.curva`, **una palabra
escrita a mano por fila**. Ahora la pertenencia se DERIVA de los ejes, con la
misma función que usa la vista (`curvas_ejes.pills` → `curvas_de`).

Tres cosas que rompían y no daban error:

  · un **dual** sólo podía tener una palabra → se escondía de una de sus dos
    tablas, y su TEA no entraba a una de las dos matrices de forwards;
  · los **6 corporativos en USD** estaban escritos como `soberanos` porque no
    había otro lugar donde ponerlos;
  · dar de alta un bono y **olvidarse la palabra** lo hacía invisible, en silencio.

Medido antes de tocar (diag read-only, ya cumplido y borrado): cer 22→25,
dolar_linked 7→31, soberanos 21→129, tamar 5→18, tasa_fija 11→13.

**`on_energia` / `on_finanzas` / `on_otros` dejan de ser curvas.** Nunca lo
fueron: eran el SECTOR del emisor metido dentro del nombre de la curva. Ser
corporativo es un EJE (`emisor_tipo`), y la curva de una ON en USD a tasa fija es
`soberanos` — que es contra quién se compara su rendimiento. Las 8 llamadas
`por_curva_like('on%')` / `not_like('on%')` (ONs, renta fija, acreencias, Manager
→ BONOS, discovery 1816) pasan a **`corporativos()` / `no_corporativos()`**, y las
dos funciones `*_like` se **borraron** para que nadie las reintroduzca.

Tres lugares comparaban `doc['curva'] != X` teniendo el doc en la mano y habrían
contradicho al listado (un bono ofrecido por el combo, rechazado al guardarlo):
`comparar_inversion` ×2 (service borrado el 2026-08-30 con la vista ESTRATEGIA) y
`breakevens_admin`. Los tres pasaron a usar
**`curvas_sql.esta_en_curva(doc, curva)`** — el predicado existe una sola vez.

⚠️ **Un bono sin ejes no cae en ninguna curva y desaparece de todo lo que llame a
`por_curva`.** Es a propósito (antes caía en la curva que dijera su palabra aunque
nadie lo hubiera clasificado), pero hay que completarlos: **10 bonos** —
`BA37 · BB37 · SA24 · SF27 · RMJ28` (provinciales) y
`NZC30 · IR2PO · PN430 · VSCWO · Y134O` (corporativos). Se listan con
`curvas_sql.sin_curva()`. `RMJ28` es distinto: SÍ tiene ejes, pero su `ajuste` es
`badlar`, que todavía no tiene curva (igual que `tpm` y `caucion`).

**Lectores de `curva` que quedan** (la columna todavía no se puede borrar):
`engines/curvas.py` (`curva_depende_de` / `dep_tasa_disponible` — de qué feed
depende la TEA), `jobs/backfill_tasas.py`,
`api/services/titulos_flujos.py`, y la clave persistida de las 4 tablas
particionadas (fase B2-B5, el único paso irreversible).

#### Paso 15 (2026-08-16) — **el MOTOR calcula por los EJES**

El último lector grande de `curva` era el propio cálculo. La TEA no tiene una sola
fórmula —Lecap, CER, hard dólar, DL y ON son cinco cuentas distintas— y el motor
elegía cuál usar leyendo **la palabra `curva`**. Ahora la elige `rama_calculo`
desde `emisor_tipo`/`moneda_eje`/`ajuste`.

**Se midió TODO antes de tocar una línea**, con tres diags encadenados (los tres
ya cumplieron y se borraron — REGLA #5; queda lo que contestaron):

| qué se midió | qué contestó |
|---|---|
| qué fórmula elegiría cada bono con los ejes | 194 bonos elegirían la MISMA, 18 otra, 9 no se puede decidir |
| correr `calcular_campos` con las DOS ramas sobre el mismo bono | 14 perdían la TEA al cambiar de rama |
| convertir los flujos en memoria y remedir | **7 vuelven a su TEA exacta (Δ = +0 bps)** |

**Dos hallazgos que no estaban en el plan.**

1. **Las ramas no se diferencian solo por la matemática, también por CÓMO LEEN LOS
   FLUJOS** (`soberanos`/`cer` en porcentaje, `on`/`tasa_fija` en absoluto). Y ese
   formato es cómo se cargó el bono, no una propiedad de sus ejes. Por eso 14
   bonos perdían la TEA **en las dos direcciones**: un problema de DATOS
   disfrazado de problema de lógica.
2. **`cupon_sobre_residual` significa cosas distintas según la rama**: en
   `soberanos` es un monto que se divide por 100; en `cer` es una TASA que se
   multiplica por el residual vivo. La primera conversión que se escribió estaba
   mal por eso (un cupón de 2 daba 200) y **no se veía leyendo el código** — lo
   cazó un chequeo numérico contra las funciones reales del motor.

**Orden de la migración — EXPANDIR → MIGRAR → CONTRAER.**
`scripts/backfill_flujos_porcentual` (paso 1, aplicado) agrega las claves
porcentuales **sin sacar las absolutas**: con las dos puestas las DOS ramas leen
bien y el orden deja de importar. Si el backfill hubiera borrado las viejas, el
bono se quedaba sin TEA en el momento de correrlo, no al migrar. Falta el
CONTRAER (limpiar las claves viejas), que no corre apuro.

⚠️ **Fallback deliberado**: sin ejes, `rama_calculo` cae a la palabra vieja. Es la
rampa para los 9 sin clasificar — sin eso perderían la TEA que hoy muestran. Se
borra junto con la columna cuando no quede ninguno.

⚠️ **El invariante más frágil, congelado por test** (`test_rama_calculo.py`): con
los ejes, un corporativo en USD a tasa fija cumple la condición de `soberanos` Y
la de `on`. **`corporativo` se pregunta PRIMERO.** Si alguien reordena, las ~140
ONs en dólares cambian de fórmula de un día para el otro, sin error.

**Lo que quedó sin TEA, con el OK de la mesa:** CP36O · MGCOO · VSCYO · VSCZO
(corporativos que salen de la curva soberana) · PMA28 · CO3D7 · TMF27 · CO2D7 ·
RMJ28. Los tres últimos mostraban números basura (−25%, +29%, −38,8%): ahí perder
la TEA es una mejora. Los cuatro corporativos necesitan un diag propio que
instrumente por dónde se corta el cálculo — la conversión de flujos corre bien,
así que el bloqueo es otra cosa.

#### Paso 16 (2026-08-16) — la VISTA también selecciona por los EJES (y el agujero que abrió el borrado de `/ons`)

**Bug propio, encontrado al revisar el paso anterior.** Al borrar la vista `/ons`
se dijo que los corporativos se verían en la tabla HARD DOLAR de `/renta-fija`.
**No era cierto**: `_fetch_curva_docs` hacía `WHERE curva = 'soberanos'` —la
columna escrita a mano— y un bono con `curva='on_energia'` no puede matchear eso.
Resultado: los ~140 corporativos quedaron **fuera de toda la app** (ni en su vista
vieja, que ya no existe, ni en la nueva).

Arreglado con `curvas_ejes.sql_universo(curva)`, el predicado de **VISTA** que ya
estaba construido y verificado. Efecto medido: **HARD DOLAR pasa de 21 a ~129
bonos** — los soberanos de siempre, más las ONs en dólares, más los BOPREAL.

⚠️ **VISTA ≠ FIT, y la diferencia no es cosmética.** La VISTA junta emisores a
propósito: el trader quiere ver el corporativo al lado del soberano para comparar
rendimientos. El **FIT** (fair value, z-score, forwards) usa
`sql_universo(fit=True)`, que exige `emisor_tipo='soberano'` — un corporativo
tiene spread de crédito y meterlo al ajuste corre la curva para TODOS, con un modo
de fallar mudo (el ajuste sale, el z-score sale, y los números son otros).

**`emisor` pasa a viajar en TODAS las tablas**, no solo en la de ONs. Con 129
bonos en una tabla, saber de quién es cada papel dejó de ser un adorno: es lo que
distingue un soberano de una ON de Vista en la misma grilla.

**Lección de proceso:** borrar una vista no es solo borrar sus archivos. Hay que
verificar que su contenido siga alcanzable desde donde uno dijo que estaría —
"se ve en la otra pantalla" es una afirmación sobre el código, y como toda
afirmación había que medirla antes de decirla.

#### Paso 17 (2026-08-16) — tasas que son ruido, el filtro que mentía y el TC BE perdido

Tres cosas que saltaron mirando la pantalla con las ONs ya absorbidas.

**1. Las TEA de los papeles ultra-cortos son artefactos, no rendimientos.** AFCHO
(vencía en 3 días) mostraba **TEA 142,1%**; CS450 **−49,1%**; HBCAO **−25,0%**.
No están mal calculadas: anualizar 3 días amplifica una diferencia de centavos a
tres dígitos. Y uno solo de esos puntos **estiraba el eje Y del gráfico hasta
aplastar a los otros 120 bonos contra el cero**, con todas las etiquetas encimadas.

El backend marca `tasa_ruido` cuando `duration < DUR_MIN_TASA` (0,05 ≈ 18 días).
Se decide por DURATION y no por días al vencimiento porque la duration ya pondera
el flujo: un bullet a 10 días y un amortizante que paga casi todo la semana que
viene tienen el mismo problema, y la fecha de vencimiento no lo dice.

**La tasa NO se borra: se marca.** La tabla la muestra apagada con el motivo en el
tooltip; el gráfico simplemente la excluye (el aviso «N fuera de escala» se sacó
a pedido del user: ocupaba una línea y no aportaba).
Ocultar el número sería mentir por omisión — el bono existe y tiene precio; lo que
no es comparable es su tasa. Y descartar puntos en silencio haría pensar que el
bono no está. La marca vive **server-side** para que la tabla y el gráfico no
puedan contradecirse.

**2. El filtro EMISOR se contradecía con la pantalla.** Con las 4 pills apagadas,
"ninguno seleccionado" significaba "todos" y la tabla igual mostraba 129 bonos.
Peor: como en ARS mandan los soberanos y en USD los corporativos, **parecía un
filtro aplicado al revés**. Ahora el último activo no se puede apagar → lo que se
ve es siempre lo que está encendido. El default sigue siendo `soberano`, para los
dos lados.

**2.b La LEYENDA del gráfico ES el filtro.** En HARD DOLAR conviven ~8 industrias
y el scatter era ilegible. **Un click en una familia la AÍSLA; click en la aislada
vuelve a todas.** Se usó la leyenda en vez de agregar una fila de controles: la
vista tiene poco alto y lo que sobra son datos, no espacio.

Tres detalles que hacen que funcione y que no son obvios:
- El aislado se aplica **al construir los puntos**, no al dibujarlos: así el eje Y,
  el fit y las etiquetas se recalculan para lo que quedó. Filtrando solo en el
  render, aislar FINANZAS dejaría la escala de los 129 bonos y se vería una raya.
- La leyenda se arma con **todas** las familias, no con las visibles. Si saliera de
  `tipos`, al aislar una la leyenda se autodestruía y no había cómo volver.
- Cambiar de pill **limpia el aislado**: las familias de TASA FIJA no son las de
  HARD DOLAR, y un aislado heredado dejaba el gráfico vacío sin pista del porqué.

A qué familia pertenece un bono vive en **una sola función** (`familiaDe`), usada
por los puntos y por la leyenda: si cada uno lo derivara a su manera, la leyenda
podría ofrecer un filtro que no matchea ningún punto.

**3. Volvió TC BREAKEVEN a TASA FIJA.** Estaba en la tabla vieja y nunca se
implementó en la nueva — el comentario del componente lo mencionaba pero el
`<thead>` no lo tenía, y el endpoint tampoco lo mandaba. Se calcula server-side
(`TC_BE = MEP × flujo_vencimiento / precio`), solo donde el flujo final está
determinado, y el MEP se lee **una vez por request** e **se inyecta** a `_armar`
para no romper su pureza (esa función se testea sin base). Sin MEP el campo sale
`null`, nunca 0 — un 0 en pantalla se leería como un tipo de cambio.

#### Paso 18 (2026-08-16) — los TAMAR y el MARGEN, traídos de 1816 (y los duales de yapa)

**El agujero.** Los 18 bonos con pata TAMAR no tenían **ningún cálculo**: caen en
el `else` de `engines/curvas.py`, que solo computa duration. No estaban mal
valuados — estaban **sin valuar**, y eso no se veía porque una celda vacía no
rompe nada. Peor: de un TAMAR lo que la mesa mira no es tanto la TEA sino el
**MARGEN sobre la TAMAR** (cuánto paga por encima de la tasa del BCRA), y ese
concepto directamente no existía en el sistema.

**Por qué NO lo calculamos.** Un TAMAR es una nota de tasa **promedio**: el cupón
promedia la TAMAR de bancos privados entre T−10 hábiles de emisión y T−10 del
vencimiento, más un margen fijado en licitación — la parte ya observada está
congelada y la futura hay que proyectarla. Medido contra la planilla de la mesa:

| | 1816 | planilla de la mesa |
|---|---|---|
| TXMD9 TEA | 38,62% | 38,55% |
| TXMD9 margen | 9,73% | 9,71% |
| TXMD9 precio | 84,10 | 84,10 |

O sea que **la mesa ya valida contra 1816**. Reimplementar la metodología nos
pondría a competir con el número que ellos ya miran, y 3 puntos básicos de
diferencia alcanzarían para que nadie use el nuestro aunque tuviera razón.

**Y los DUALES se resuelven con la misma llamada.** 1816 publica cada pata como
un ticker aparte, y **rinden distinto de verdad**: TXMD9 da **6,82%** por CER
(tasa REAL, sobre inflación) contra **38,62%** por TAMAR (nominal) — ~2.900 bps.
Hasta hoy la vista mostraba **el mismo número en las dos tablas**, porque
`mercado.market_snapshot` tiene una fila por símbolo y por lo tanto una sola TEA.
Por eso `mercado.tamar_1816` tiene PK **(ticker, pata)**: es la estructura que el
snapshot no puede representar.

**Dónde se escribe (dos destinos, con una regla que los separa).**

1. **`mercado.tamar_1816`** — una fila por PATA. Es la única estructura que puede
   representar los dos rendimientos de un dual, y la que lee la tab CURVAS.
2. **`mercado.market_snapshot.tea` (+ `tem` derivada)** — pero **SOLO** los bonos
   con `ajuste = 'tamar'`, o sea la pata PRINCIPAL. Ese es exactamente el conjunto
   donde el motor cae en la rama `otros` y no calcula tasa: **es imposible pisarle
   un número, porque para esos bonos no produce ninguno.** Sin este segundo
   destino, los TAMAR seguirían vacíos en la tabla RENTA FIJA, forwards, fair
   value y sensibilidad — todos leen `market_snapshot`, no la tabla nueva.

Un dual CER+TAMAR (`ajuste='cer'`) **no** entra al snapshot: ahí el motor sí
calcula, y en vivo, y esa es la tasa correcta para la tabla vieja. Su pata TAMAR
vive solo en `mercado.tamar_1816` y se ve en la tab CURVAS, que es la única que
sabe mostrar dos. `duration` tampoco se toca: esa el motor sí la computa.

⚠️ **Contrato con el motor, congelado por test.** Esto es seguro mientras
`dep_tasa_disponible('otros', …)` sea `False`. Si devolviera `True`, el
anti-TEA-fantasma pondría `tea = NULL` en cada vuelta —cada 5 segundos— y la única
señal sería que la columna vuelve a estar vacía: sin excepción, sin log, sin nada.
`test_la_rama_otros_NO_habilita_el_anti_TEA_fantasma` falla si alguien lo cambia.

**Qué fuente gana en la LECTURA — la regla, en una línea: 1816 solo aparece donde
el motor no puede.** No es "1816 manda porque es más consistente":

  · **`ajuste = 'tamar'` y emisor NO corporativo** → manda 1816, con **TEA, TNA,
    duration y paridad**,
    no solo la tasa. El motor cae en la rama `otros` y no calcula nada ahí, pero
    lo que quedó en el snapshot **no está vacío: es basura de un cálculo viejo**
    (el anti-TEA-fantasma no limpia esa rama, así que sobrevive un valor anterior
    a la migración de ejes). Visto en pantalla: **TMF27 con TEA −25,0%, TEM
    −2,37% y MOD DUR 0,70**. Se descartan también `TEM`/`mod_duration`/
    `convexity`: dejar una derivada del motor al lado de una duration de 1816
    mezcla dos cálculos en la misma fila y nadie podría decir cuál está mal.
  · **pata SECUNDARIA** → el snapshot tiene la tasa de la OTRA pata: se DESCARTA
    **haya o no reemplazo**. Sin dato de 1816 la celda queda **vacía**.
  · **el resto** (CER, dólar linked, tasa fija) → el motor, **intacto**. 1816 no
    se mete aunque tenga el dato: su número es LIVE y el del proveedor tiene
    media hora de atraso. Cambiar uno por otro es empeorar la pantalla para ganar
    consistencia con un tercero.

El punto de la pata secundaria era un bug real y estaba en pantalla: TTD26/TTS26
son TAMAR+FIJA y 1816 no publica su pata fija, así que en la tabla TASA FIJA
aparecían con la TEA de la pata TAMAR (28,6%) como si fuera suya.

⚠️ **La excepción del CORPORATIVO no es un detalle de borde.** `rama_calculo`
pregunta por el emisor ANTES que por el ajuste, así que un TAMAR de emisor
corporativo va a la rama **`on`**: el motor **sí** lo calcula, y en vivo. La
primera versión de esto no lo contemplaba, y el resultado no era "un número
peor" sino una **pelea**: el job escribía la tasa de 1816 en el snapshot, el
motor la recalculaba con el siguiente trade, y lo que se veía dependía de quién
guardó último — además de que la tab CURVAS y la tabla RENTA FIJA podían mostrar
dos tasas distintas para el mismo bono. Hoy afecta a **ZPC1O**, el único
corporativo con pata TAMAR que 1816 cubre. El MARGEN de esos bonos sí se muestra:
lo publica solo 1816 y no compite con nada.

**Residuo conocido**: un TAMAR **no corporativo** que 1816 tampoco cubra sigue
mostrando lo que el motor haya dejado. Hoy es **CO2D7** (provincial), uno solo.
No se lo limpia desde la vista porque para saberlo habría que replicar acá la
lógica de ramas del motor — una segunda copia que puede divergir. Se resuelve
solo el día que la lógica TAMAR propia exista.

**La CURVA de los TAMAR se grafica por MARGEN, no por tasa.** Los ocho flotan
contra la MISMA referencia (la TAMAR del BCRA), así que su TEA nominal se mueve
toda junta y ordenarlos por ella no dice nada del papel: lo que distingue a un
TAMAR de otro es cuánto paga POR ENCIMA de esa referencia. Por eso MARGEN no es
una opción del selector de métrica sino la **única** de esa pill, el eje Y se
rotula «Margen s/ TAMAR», y el modo HISTÓRICO cae a LIVE (la serie histórica
guarda TEA: el eje diría una cosa y los puntos serían otra). El bono sin margen
**no se grafica en cero** — un 0% ahí se leería como «paga la TAMAR pelada».

**La procedencia se marca por FILA, no por tabla.** Cada bono lleva `tea_fuente`
(`null` = motor), `tea_fecha` y `pata`, y el front pone un `*` al lado de la TEA
con el motivo en el tooltip. Un cartel arriba de la tabla estaba mal por dos
razones: el hecho es por fila (**un solo** dual TAMAR+DOLAR LINKED —TMVE8—
prendía el aviso en TODA la tabla de DOLAR LINKED, donde el resto sí es live) y
esta pantalla se le pasa a clientes, así que un renglón de texto la ensucia.

**Es un PARCHE con salida limpia** (decisión del user: la lógica TAMAR propia se
va a implementar). El día que llegue: `rama_calculo` deja de devolver `otros` para
los TAMAR, se **apaga el cron** y listo — el motor pasa a llenar el snapshot y la
regla de arriba deja de encontrar filas de 1816 sola. `mercado.tamar_1816` puede
quedar como contraste contra el número del proveedor.
`ce.pata_de_pill()` —la **inversa** de `pills()`, en el mismo módulo y reusando
`_pill_de_ajuste`— es lo que dice qué pata corresponde a cada tabla; con un
segundo criterio, el bono mostraría la tasa de la otra pata y nada fallaría.
Un test barre el dominio completo de ejes verificando que toda pill sepa su pata.

**Cuatro trampas que costaron una corrida cada una:**

1. **`indicadores` sin `fechaOperacion` usa HOY.** Un domingo devolvió los 6
   campos en `null` y pareció que el campo `spread` no existía. Estaba avisado en
   el docstring del cliente. Se manda **siempre** fecha explícita y se retrocede
   día hábil por día hábil si vuelve vacía (eso cubre feriados y las corridas de
   antes de las 11 ART, cuando la rueda de hoy todavía no existe).
   **Desde el 2026-08-17 eso vive en `core.mercado_1816.indicadores_vigentes`**,
   no dentro del job: el pre-flight del AV Agent necesitaba lo mismo y dos
   criterios para la misma pregunta terminan siempre con uno de los dos viejo.
   El que llame a `indicadores` directo se vuelve a comer la trampa.
2. **El campo del margen es `spread`.** `margen`, `margin`, `spreadTamar` y
   `margenTamar` devuelven HTTP 400. Se probaron **de a uno**: la API rechaza la
   llamada entera si un campo no existe, así que en lote no se sabe cuál falló.
3. **Escala: FRACCIONES.** 1816 manda `0.0973` para «9,73%», igual que nuestro
   `market_snapshot.tea`. Se guarda tal cual — convertir habría dejado dos escalas
   conviviendo, que es el bug que suma bien de los dos lados y da distinto al
   comparar.
4. **La grafía la manda el catálogo, no nosotros.** Concatenar `f"{tk} @TAMAR"`
   parece obvio y es frágil. Se leen las variantes de
   `research.mkt_1816_instrumentos`. Caso real: en TTD26/TTS26 el catálogo guarda
   `@TASA FIJA` pero la denominación dice `@BONCAP`, y con la primera 1816 no
   devuelve nada → el job pide **las dos** y gana la que trae tasa.

**Lo que 1816 NO cubre y hay que saberlo**: de los 9 corporativos con pata TAMAR
volvió **solo ZPC1O**. Los otros 8 y el provincial CO2D7 quedan sin tasa y sin
margen — celda vacía, que es honesto. El job lo cuenta (`sin_dato` en
`manager.job_runs`) para que la caída se vea en vez de descubrirse mirando.

**Pendiente**: los TAMAR siguen sin TEA en `get_renta_fija` (la tabla vieja)
y forwards — esos leen `market_snapshot`, que no se toca. Solo la tab CURVAS los
valúa hoy.

Cron: **cada 30′ de 10 a 17 ART, L-V** (`0,30 13-19` + `0 20` UTC), ~150 créditos
por corrida ≈ 2.250/día de los 100.000 diarios.

#### Paso 19 (2026-08-18) — el LIBRO vuelve, y sale de la vista a una VENTANA FLOTANTE

**Lo que pasó.** La tabla vieja (`renta-fija-table.tsx`) tenía cinco pills:
`TASA FIJA · CER · HARD DOLAR · DOLAR LINKED · **LIBRO**`. Cuando la tab CURVAS
la reemplazó (paso 3b), las cuatro primeras se reconstruyeron sobre
`curvas-vista` y **la quinta no**: `libro-panel.tsx` quedó en el repo, intacto y
sin que nadie lo montara. No hubo error, ni build roto, ni endpoint caído —
simplemente el botón dejó de estar. Es el mismo patrón de los pasos 10-13: *nada
falla, y por eso no se ve*. Reportado por el user, no por el sistema.

**Repuesto y REUBICADO.** La primera versión lo trajo de vuelta como una pill más
del lado ARS. El user marcó dos cosas y son **la misma**:

1. pegado a `TASA FIJA / CER / TAMAR / BADLAR` se lee como **un ajuste más**, y no
   lo es — esas cuatro son cortes del mismo dato, el libro es otra herramienta;
2. al ocupar el panel te **tapaba la tabla y la curva**, que son justo contra lo
   que uno compara el tape.

Las dos se resuelven sacándolo de la grilla: **botón a la DERECHA del header
(`Panel.rightActions`, prop nueva y aditiva) que abre una VENTANA FLOTANTE**
(`ventana-flotante.tsx`). La columna ARS conserva SIEMPRE su tabla + su curva.

**La ventana**: se arrastra de la barra, se redimensiona de la esquina, cierra
con ✕ o **Esc**, y **no es un modal** — sin backdrop, no bloquea el fondo ni roba
el foco, así que con el libro abierto se sigue cambiando de pill, de emisor y de
tab. Va por `createPortal` al `body`, así que **no le saca ni un pixel a las
columnas**. Se desmonta al cerrar (el poll de trades no queda corriendo
escondido). La **geometría** se persiste en `localStorage`
(`rentaFija.libro.ventana`): dónde la ponés es una preferencia, no un filtro.
Abierto/cerrado **no** se persiste — que la app te abra sola una ventana que no
pediste es peor que un click.

**Universo**: el lado **ARS entero**, no una pill — el tape se busca por TICKER y
cortarlo por ajuste obligaría a saber de antemano si el bono es CER o tasa fija.
Respeta el filtro de EMISOR, así que con el default (`soberano`) la lista son
exactamente los soberanos ARS. Se filtra por `lado` y no por `moneda` (un dual
TAMAR+DOLAR LINKED es ARS y tiene una fila de cada lado), se **deduplica por
instrumento** (un dual llega repetido, una fila por pata → el selector mostraría
el ticker dos veces) y entran solo los que tienen `last_price`. Ese último filtro
es lo que la tabla vieja hacía con `flujos` (bonos VIVOS), sin volver a pedir los
240 KB de cronogramas que el paso 3a eliminó.

**Cero backend**: `/api/cotizaciones/historico/trades` nunca dejó de existir.

**Dos trampas de front que costaron una corrida cada una** (verificadas en browser
con Playwright, no razonadas):

1. **`typeof document === "undefined"` NO es un guard válido de portal.** El
   server renderiza `null` y el cliente la ventana en el mismo paso → *"Hydration
   failed: server rendered HTML didn't match"*. Hoy no se dispara porque la
   ventana nace cerrada, pero el primero que la abra por default —o que persista
   el abierto/cerrado— se lo come sin entender por qué. Va el patrón correcto:
   estado `montado` en un `useEffect`. (`panel.tsx` tiene el guard viejo en su
   overlay de expandir; ahí está tapado por el mismo motivo.)
2. **Dos `ml-auto` compitiendo no alinean a la derecha: REPARTEN.** Con
   `rightActions` y el botón de expandir los dos con `ml-auto`, flexbox parte el
   espacio libre entre ambos y las acciones quedan flotando en el medio (medido:
   x=695 de 1100). El botón de expandir pasa a `ml-1` **solo cuando hay
   `rightActions`** → los ~12 paneles que ya usan `actions` quedan idénticos.

Verificado en browser real: arrastra, redimensiona, el fondo sigue clickeable, la
posición sobrevive al F5 y Esc cierra. Consola sin errores ni warnings.

Archivos: `ventana-flotante.tsx` (nuevo), `curvas-tab.tsx`, `panel.tsx`
(prop `rightActions`, aditiva). `renta-fija-table.tsx` sigue **huérfano** — ya no
lo monta nadie; se borra cuando el paso 4 cierre las tabs viejas.

### Paso 8 — las PATAS (`mercado.especies`, 2026-08-15)

Pregunta del user: *"¿no debería cada asset tener su instrumento ARS y su
instrumento USD? Ahora solo hay uno, uniforme, le faltan datos."* Correcto — y el
modelo **ya existía a medias**: `api/services/ons.py:170` guarda cada ON con
`tickers: {"ARS": …, "USD": …}`, pero la línea 141 elige UNA ("pata canónica por
moneda: USD → ticker D, ARS → ticker O") y solo esa llega a la columna. La otra
queda enterrada en el blob.

```
portafolio.assets   EL ACTIVO   AL30    emisor, cartera, calificación
mercado.curvas      LA CURVA    AL30    flujos + ejes
mercado.especies    LA PATA     1 a N   simbolo · ticker · ticker_especie · moneda · plazo · es_default
```

**`ticker_especie` (AL30D) NO es basura a limpiar.** Es la identidad de lo que el
cliente TIENE y COBRA: medido, 9.726 filas viven con ese label
(`operaciones.acreencias` 7.127, `portafolio.tenencia` 620,
`mercado.snapshots_cierre_hist` 1.539, …) y ahí está BIEN — se tiene la especie D,
se cobra en la especie D. Lo que está mal es que el MISMO campo haga de label del
bono en `curvas`. Por eso la tabla guarda las dos claves: `ticker` une con la
curva, `ticker_especie` une con la posición.

**Medido contra Primary** (`manager.pyrofex_instruments`, 9.719 símbolos — la
fuente que NO depende de lo que elegimos nosotros, a diferencia de
`market_snapshot`, que solo tiene lo que el motor suscribe *desde el master*):

- **17 bonos tienen las 3 especies** (pesos/MEP/cable × 24hs y CI); **183 tienen
  una sola**; 21 no aparecen. O sea que hoy es 1:1 para casi todos — el 1:N es
  correcto igual, y es lo que deja de perder datos.
- **UN solo bono está cruzado: `CO32`** (`on_otros`, denominado en USD, apuntando
  a la especie en PESOS). Ese es el precio de otra escala. Uno, no diecisiete.

**Dos cosas que la primera corrida destapó** (y que el script tenía mal):

1. **El catálogo de Primary queda VIEJO, y eso no invalida el símbolo.** `AO29` no
   figura en `manager.pyrofex_instruments` y sin embargo, mandado a mano, devuelve
   precio. Por eso la pata que el master usa HOY **se siembra siempre**, figure o
   no en el catálogo: si no, sembrar borraría el símbolo que la vista está usando y
   un catálogo atrasado le ganaría a la realidad. Refrescarlo:
   `python -m scripts.discovery_pyrofex`.
2. **Un cruce solo es cruce si hay a dónde apuntar.** La primera versión marcó
   **137 falsos positivos** porque exigía que un bono en USD usara especie MEP o
   cable sin chequear que existieran. Las **ONs no tienen pata D**: su ticker YA
   termina en `O` (`AER9O`), que es parte del NOMBRE y no un sufijo de especie. Un
   hard dollar corporativo cotizando en su única especie no está cruzado. Con la
   condición corregida vuelve a dar lo que midió el bloque 8 del diag: **CO32**.

3. **Conviven DOS convenciones de nomenclatura**, y mezclarlas clasificaba mal la
   pata en dólares de las ONs:

   | | pesos | dólares | cable |
   |---|---|---|---|
   | **soberanos / letras** — la especie es un SUFIJO | `AL30` | `AL30D` | `AL30C` |
   | **ONs** — la especie es la ÚLTIMA LETRA del ticker | `AERBO` | `AERBD` | — |

   `AERBD` no matchea la regex de sufijo (no tiene dígitos antes de la `D`) y caía
   a PESOS **siendo la pata en dólares**. La segunda convención se **reconoce, no
   se adivina**: aplica solo cuando el par `stem+O` / `stem+D` existe de verdad en
   el universo (Primary ∪ master). La documenta `ons.py:136`.

**Resultado, tras refrescar el discovery** (`python -m scripts.discovery_pyrofex`,
12.882 instrumentos): **2 cruzados — `AO29` y `CO32`**, los dos denominados en USD
y apuntando a su especie en PESOS. `AO29` es el que el user venía reportando desde
el principio ("muestra ~141.430 al lado de bonos en ~90"): no se detectaba antes
porque el catálogo de Primary estaba viejo y no lo listaba.

**Resultado final, con el discovery al día** (12.882 instrumentos): **758 patas**
para 221 bonos, **0 sin default**, **36 con el default cruzado** y **13 fuera del
catálogo**. De los 36, **`AO29` es el que el user venía reportando desde el
principio** ("muestra ~141.430 al lado de bonos en ~90").

**Corregir un cruce NO es un backfill masivo** (`--corregir TICKER[,TICKER]`, y solo
junto con `--aplicar`). Repuntar `curvas.instrumento` cambia **lo que el motor
suscribe**: un bono cuya pata en dólares casi no opere pasaría de mostrar un precio
en otra escala a **no mostrar ninguno**. Eso se decide caso por caso mirando el
mercado, no desde un script — por eso el flag exige tickers explícitos y nunca
acepta "todos".

> ⚠️ **Las curvas `on_*` fracasaron y las ONs se van a rediseñar** (decisión del
> user, 2026-08-15): `on_energia` / `on_finanzas` / `on_otros` nunca se usaron. **El
> modelo de especies es ORTOGONAL a eso**: `mercado.especies` no guarda ni una
> referencia a `curva`, así que el rediseño de ONs puede reagrupar como quiera sin
> tocarlo. Los **34 cruces que son ONs quedan a la espera** de ese rediseño —
> corregirlos ahora sería trabajo que se rehace. Los 2 que NO son ONs (`AO29`
> soberano y `CO32`) se pueden corregir ya.

El check `especies_cruzadas` de `jobs/guardrails.py` se dio de baja el 2026-09-02 con el job entero (`docs/AGENT.md` §0.dj): nació sin calibrar y nunca marcó nada. El cruce lo mira `precio_moneda` (regla `pata_equivocada`).

**Estado (2026-08-15, aplicado):** 758 patas sembradas · `AO29` y `CO32`
repuntados a su pata en dólares · **34 cruces pendientes, todos ONs**.

⚠️ **Lo que TODAVÍA no se hizo: limpiar el valor de `curvas.ticker`.** El renombre
del paso 6 cambió los NOMBRES de las columnas, no los VALORES: 17 bonos siguen
teniendo `AL30D` como PK, y por eso la vista sigue mostrando `AL30D` en la columna
TICKER. Ahora sacar el sufijo **es seguro** —`mercado.especies.ticker_especie`
guarda la identidad de la pata, así que la D ya no se pierde— pero es un paso
propio (paso 9): toca la PK y hay que mover con ella los 4 `portafolio.assets` que
la arrastran.

Siembra: `python -m scripts.sembrar_especies` (DRY-RUN) / `--aplicar`.

### Paso 8.b — `assets` pasa a DERIVAR de especies (2026-08-15)

Primer lector de la tabla, y el que cierra el pedido de fondo: **un solo lugar
donde vivan los instrumentos**.

`portafolio.assets.instrumento` no es decorativo — es el símbolo que el motor de
portfolio le SUSCRIBE a Primary (`engines/_universo_portfolio.py`), o sea de dónde
sale el `last_price` de toda la tenencia. Se cargaba **a mano** en Manager, así que
el catálogo de market data terminó desparramado en tres lugares que se
contradicen: `assets`, `mercado.curvas` y el universo real de Primary. El bloque 9
se midió en prod: **65 assets con un símbolo distinto al del master**, y
los 65 con tenencia (24.521 filas).

Ahora `assets` es un **derivado**. La regla `especies` de `jobs/assets_autofill`
relaciona por **TICKER** —la bisagra del modelo, el mismo `AL30` en `assets`,
`especies` y `curvas`— y baja las DOS patas a columnas explícitas:

| columna | qué es | de qué `especie` sale |
|---|---|---|
| `instrumento` | la pata en PESOS | `pesos` |
| `instrumento_usd` | la pata en DÓLARES | `mep` |

El **cable NO entra**: es otra cosa, y meterlo en la misma columna volvería a
esconder cuál es cuál — que es exactamente el problema que se está cerrando.
Dentro de cada pata gana la `es_default` (la que la mesa ya eligió para la curva) y
después **24hs sobre CI**, que es donde hay liquidez y por lo tanto precio.

**Por qué esto no puede romper una valuación.** Rige el invariante del job:
**nunca pisa**. Lo que hoy está cargado queda como está y el motor suscribe
exactamente lo mismo — el cambio agrega información, no la reemplaza. Cuando lo
cargado no coincide con especies se REPORTA como conflicto en el run, que es
justo el listado que no existía y por el cual el cruce de `AO29` sobrevivió meses.
Los dos campos siguen editables en Manager → TÍTULOS · ASSETS: el catálogo de
Primary a veces está viejo (`AO29` no figuraba y sin embargo devuelve precio) y
ahí manda la mesa.

**Lo que queda abierto:** de los 65 divergentes no está medido *cuál* pata es la
correcta para el PnL. Hipótesis fuerte (no verificada): la de PESOS, porque el PnL
trabaja pesificado (`pnl.py::_pesificar` convierte el cost-basis con el MEP del
boleto). Se midió valuando con las dos patas y comparando (diag ya cumplido y borrado);
las contrasta contra la `valuacion` que manda Aunesa — la buena da ratio ≈ 1.
**No bloquea nada**: mientras el job no pise, la respuesta sólo decide si además
hay algo viejo que corregir.

### Paso 6 — los nombres de `mercado.curvas` (2026-08-15)

`ticker_corto` (la PK) **era** el ticker del bono y `ticker` **era** el símbolo de
mercado. Quedó al derecho:

| antes | ahora | qué es |
|---|---|---|
| `ticker_corto` (PK) | **`ticker`** | `AL30` — joinea con `portafolio.assets.ticker` |
| `ticker` | **`instrumento`** | `MERV - XMEV - AL30 - 24hs` — el símbolo que se le manda a Primary |
| `instrumento` (eje bono/letra) | **eliminada** | tenía que liberar el nombre, y estaba vacía |

El eje **bono/letra se eliminó** (mismo día). Nació con el rediseño porque 1816
tiene curvas que lo nombran (Botes, Letras CER, Lelink), pero son **3 de sus 28**:
en las otras 25 la fuente no lo afirma y la columna quedó **vacía en los 221
bonos**. Un eje que casi nunca se puede completar no parte el universo en dos, lo
parte en "algunos" y "no sé" — y obliga a todo el que lee la tabla a preguntarse
qué significa. Se borró la columna, el campo de `Ejes` y `instrumento_tipo` de la
respuesta de la vista. **Ningún bono cambió de pill** (congelado por test): una
letra CER sigue siendo CER y un Bote sigue siendo tasa fija.

⚠️ Al borrarla hubo que desarmar el **paso 1 del bloque `DO` de renombre** en
`sql/schema.sql`: renombraba `instrumento → tipo_instrumento` bajo la condición
"existe `instrumento` y no existe `tipo_instrumento`", que es exactamente el
estado de la base ya migrada. Dejarlo habría hecho que el próximo `apply_schema`
le pusiera `tipo_instrumento` al **símbolo de mercado** — y la vista se quedaba
sin precios sin un solo error. Ahora ese paso BORRA en vez de renombrar y
distingue los dos mundos por si `ticker_corto` todavía existe.

**El blob `data` NO se tocó.** Sus claves siguen siendo las viejas y son las que
leen ~500 lugares vía `core/curvas_sql.py` (que hace `SELECT data`). Para que la
base quedara correcta sin tocar una línea de lógica, los `SELECT` directos llevan
**alias** (`instrumento AS ticker, ticker AS ticker_corto`). Es un shim explícito
del paso 6, no confusión permanente: lo saca el paso 7.

Por qué así y no todo junto: renombrar `ticker` cambia su SIGNIFICADO, y una query
que esperaba lo viejo **no falla — devuelve el dato equivocado en silencio**. El
alias elimina esa ventana. El escritor es uno solo (`ons.py::curva_doc_to_row`),
y el `DO $$` de `sql/schema.sql` es idempotente (Postgres no tiene
`RENAME COLUMN IF EXISTS`; el guard va contra `information_schema`).

**Medido antes de tocar** (diag ya cumplido y borrado, 221 instrumentos):

- `instrumento` es `MERV - XMEV - <ticker> - 24hs` en **221/221**, un solo plazo y
  un solo mercado → hoy es **derivable**, no es dato. Se guarda igual porque es la
  clave de Primary y un plazo CI lo volvería no-derivable.
- **17** tickers arrastran la especie pegada (`AL30D`); **5** tienen más de una
  especie cotizando. La tabla de especies (1:N) **no es urgente**.
- **30** instrumentos de `curvas` no tienen fila en `portafolio.assets` (casi todas
  ONs) → **`assets` todavía NO puede ser el maestro único**. Bloquea el paso 7.
- La ficha duplicada **ya divergió**: 12/103 en `emisor` (mismo emisor, otro nombre:
  `Telecom Argentina` vs `TELECOM`) y 39/167 en vencimiento — ahí conviven ruido de
  formato (`2030-06-28` vs `28/06/2030`) con diferencias reales (`VSCWO` difiere un
  MES; `GD29D`, `TZV28`, `GD38D`, un día).
- `data` pesa **132 KB = 50%** de la tabla y `flujos` otros 102 KB: es ~90% JSON, y
  `data` es la **copia #3** de la ficha.

⚠️ **Lo que NO se pudo medir**: si algún bono quedó apuntando a la especie
equivocada. El chequeo cruza contra `mercado.market_snapshot`, pero el motor
suscribe **desde el master** (`engines/_curvas_loader.py:45`), así que si el master
eligió mal la hermana correcta nunca entra al snapshot: el instrumento está ciego.
Para medirlo hace falta el universo de 1816 o `manager.pyrofex_instruments`.

**El modelo de curvas como OBJETO** (decidido con el user, deriva del cruce con
1816 — ver `docs/RESEARCH.md` §4.10):

```
nivel 1 — EMISOR   soberano · provincial · corporativo · bcra
nivel 2 — MONEDA   ARS · USD · EUR
nivel 3 — AJUSTE   fija · cer · tamar · badlar · dolar_linked · dual · tpm · caución
+ ley (local/ny, para Bonar vs Global) · instrumento (bono/letra) · sector (del EMISOR)
```

- **La CURVA es el camino completo** (`soberano › ARS › CER`) — que es,
  literalmente, cómo se llaman las 28 curvas de 1816. Ese es el puente del job.
- **La PILL de la vista NO es la curva**: es un corte por AJUSTE, y una pill
  puede juntar varias curvas. `HARD DOLAR` = moneda USD + ajuste fija y hoy ya
  junta Bonares + Globales + Corporativos USD + BCRA — por eso hay 6
  corporativos guardados bajo `curva='soberanos'`: **no es un error de carga, es
  que el campo y su uso divergieron.**
- **El sector de las ONs sale de la curva** (`on_energia`/`on_finanzas`/
  `on_otros` mezclan emisor con mercado; medido: `on_otros` se abre en 10 curvas
  de 1816 e incluye BOPREALes y provinciales). Pasa a ser atributo del emisor.

**Pills acordadas**: `TASA FIJA · CER · HARD DOLAR · DOLAR LINKED · TAMAR ·
DUALES · LIBRO`. Hoy faltan TAMAR (existe en `_CURVAS_VALIDAS` y **no tiene
botón en el front**) y DUALES (imposible: los duales están repartidos entre
`cer` (5) y `tamar` (3), no existen como concepto).

**Tab CURVAS**: la MONEDA deja de ser pill y pasa a ser el LAYOUT — izquierda
ARS (`TASA FIJA · CER · TAMAR · DUALES`), derecha USD (`HARD DOLAR · DOLAR
LINKED`), tabla arriba y curva abajo en cada lado, con pill independiente por
lado. **Absorbe la vista de ONs** (decisión del user): pasa de ~66 a ~222 bonos,
por eso necesita un **filtro de EMISOR** arriba de las pills, con el default
reproduciendo lo que se ve hoy.

**Regla de migración que no se negocia**: antes de tocar el front, un **test de
equivalencia de conjuntos** — para cada pill actual, la lista de tickers del
modelo nuevo tiene que ser IDÉNTICA a la del viejo. Si el diff es vacío, la
vista no puede cambiar. Lo corre `scripts/clasificar_curvas.py` y parte el
resultado en IGUAL / **CAMBIA** / ENTRA / FUERA: solo los CAMBIA pueden romper
algo, y cada uno tiene que ser explicable (los esperados son los duales yéndose
a su pill propia). Si aparece uno inexplicado, el semáforo da **ROJO** y el paso
3 queda bloqueado.

**Resultado del paso 2** (corrida real 2026-08-15): **212 de 222 clasificados**,
10 sin match en 1816 (se cargan a mano después). Test de equivalencia **VERDE CON
NOTA**: IGUAL 56 · **CAMBIA 5** (los duales `TXMD8/TXMD9/TXMJ0/TXMJ8/TXMJ9`
yéndose de `cer` a su pill propia) · ENTRA 150 (las ONs) · FUERA 11. El desorden
quedó medido: `on_otros` (80) contenía **BOPREALes** y provinciales, `soberanos`
(21) contenía **6 corporativos**, y `tamar` (5) era **más dual que tamar** (3 de 5).

**Paso 3a** — `api/services/curvas_vista.py` + `GET /api/cotizaciones/curvas-vista`:
la tab entera en UN request (pills + emisores + bonos ya clasificados). Mata el
fetch de `titulos/flujos` (240 KB), que existía SOLO para armar el mapa
ticker→curva en el navegador: con los ejes en la base, el backend ya sabe qué es
cada bono. El join va del master (222) al snapshot y no al revés — las 398 filas
de `market_snapshot` incluyen especies/plazos que no son instrumentos del master,
y traerlas para descartarlas en el browser es justo lo que se está sacando.
Convive con los endpoints viejos hasta que el front migre. Reparto medido:
**ARS 53 / USD 159**, y HARD DOLAR pasa de 21 a **129** filas al entrar las ONs.

**Dónde vive el modelo**: `core/curvas_ejes.py` — tabla explícita de las 28
curvas de 1816 → ejes, + la definición de las 6 pills en UN solo lugar (para que
backend, front y test no puedan contradecirse). Es lógica pura, con 12 tests que
congelan las decisiones (`tests/unit/test_curvas_ejes.py`), incluida la regresión
crítica: **`cer_fijado` es un ESTADO, no un eje** — un CER con el CER de
liquidación ya publicado se sigue mostrando en TASA FIJA, como hoy.

### Por qué tarda — MEDIDO en el Droplet (2026-08-15)

La página hace **9 fetches en un `Promise.all`** → no renderiza hasta que
termina el más lento. Corrida real:

| Endpoint | Tab | Frío | Caliente | Payload | Filas |
|---|---|---:|---:|---:|---:|
| `renta-fija` | CURVAS | **369 ms** | 0 ms | 108 KB | 398 |
| `historico/forwards` | FORWARDS | 161 ms | 0 ms | **3.751 KB** | 593 |
| `flujos` | CURVAS | 110 ms | 14 ms | 240 KB | 222 |
| `breakevens` | BREAKEVENS | 108 ms | **76 ms** | 1 KB | 1 |
| resto (5) | — | ≤88 ms | 0 ms | 550 KB | — |
| **TOTAL** | | **1.054 ms** | | **4.650 KB** | |

**La conclusión NO es la que se suponía.** Tabificar baja el peso un 92%
(4.650 → 357 KB) pero **la espera no baja** (369 → 369 ms): el cuello de botella
es `renta-fija`, que es justamente de la tab CURVAS y se pide igual.

El problema real es el **PESO**, no la query: 4,5 MB viajan Droplet → Vercel →
navegador y el browser tiene que parsear todo eso antes de pintar. 369 ms de
backend no se sienten; 4,5 MB sí. De ahí que "tarda en cargar" y no "tarda en
responder".

De dónde salen los 4,5 MB (verificado en el código):

- **`historico/forwards` = 3.751 KB, el 80% del total él solo.** `_hist()` trae
  TODO el histórico **sin filtro de fecha ni de par** (593 días × la matriz
  completa de cada día) para dibujar **una** línea de un par por vez.
- `forwards-zscore`: 214 KB en **7 filas** (30 KB por fila) — los coeficientes de
  todos los pares de las 7 curvas.
- `flujos`: 240 KB — el cronograma COMPLETO de los 222 bonos para usar 5 campos.

**El rediseño ya lo arregla solo**: con una matriz por curva los endpoints se
piden POR CURVA, y si el gráfico pide el par que se está mirando, esos 3,7 MB
pasan a ser unos KB. No es trabajo extra, es consecuencia del diseño.

**Sobre el cache**: casi todos caen a 0 ms en caliente → en uso normal la vista
va bien y **paga el primero que entra después de que expira el TTL**. Ese es el
"a veces tarda". El único que NO cachea es `breakevens` (108 → 76 ms).

**Prioridades**: (1) partir en tabs → −92% de bytes; (2) `historico/forwards`
por par y por rango → sale gratis con la tab FORWARDS; (3) `renta-fija` 369 ms,
el único independiente de las tabs.

---

> **🔜 ALTA Y FLUJOS DE BONOS — automatización con 1816 (diseño 2026-08-15).**
> `mercado.curvas` se mantiene **a mano**: cada bono nuevo de una licitación hay
> que darlo de alta y tipearle el cuadro de flujos (que además se saca de 1816).
> El diseño para automatizar eso vive en **`docs/RESEARCH.md` §4.10** —
> ahí están los números medidos (212 de nuestros 222 bonos están en 1816, 98,6%
> de cobertura de cashflow, ~29 créditos/día detectar novedades) y la decisión
> asentada: **el job PROPONE el alta, no la escribe solo** (un flujo mal escalado
> entra directo a la valuación y al AuM). Herramienta: `python -m
> un diag de mapeo (ya cumplido y borrado). Antes de tocar `mercado.curvas.curva` —el campo que
> agrupa estas vistas y arma el fair value— leer esa sección.

---

## 2. Los 4 paneles (qué muestra cada uno y con qué endpoint)

| Panel | Sub-bloques | Endpoint(s) backend | Service |
|---|---|---|---|
| **1. Tabla Renta Fija** | Tasa Fija · CER · Hard Dólar · Dólar Linked · **Libro** | `GET /api/cotizaciones/snapshot-live` (bundle, poll 5s) · `/renta-fija` · Libro: `/historico/trades` | `renta_fija.py` |
| **2. Forwards** | Live · Gráfico · Z-score | `/forwards` · `/historico/forwards` · `/forwards-zscore` | `derivados.py` |
| **3. Curvas** | 4 curvas × (Live / Histórico / Fair Value) | `/historico/curva` · `/analitica/listar-curva` · `/fair-value` · `/fair-value/historico` | `renta_fija.py` · `analitica.py` · `fair_value.py` |
| **4. Breakevens** | Live · Histórico · overlay REM | `/breakevens` · `/historico/breakevens` · `/rem/breakeven-acumulado` | `derivados.py` · `rem.py` |

> **`snapshot-live` es un bundle:** un solo endpoint que devuelve
> `{renta_fija, forwards, breakevens}` leyendo del cache de cada service (TTL
> 5/30/30s). El front hace **1 poll cada 5s** en vez de 3. Verificado en
> `api/routers/cotizaciones.py:202-226`.

Todos los endpoints listados **existen** y fueron verificados en
`api/routers/cotizaciones.py` y `api/routers/analitica.py`.

---

## 3. Tablas SQL de la vista — quién las lee y quién las llena

Verificado: acceso a cada tabla en su service `*_sql.py` + el motor/job que escribe.

### 3.1. Tablas BASE (el núcleo)

| Tabla SQL | Qué es | La leen (services) | La llena (motor/job) |
|---|---|---|---|
| **mercado.curvas** | Maestro estático de cada bono: flujos, vencimiento, cupón, `cer_emision` | renta_fija, analitica, carry_trade, sensibilidad, titulos_flujos, fair_value | Maestro editable (no lo escribe un motor) — carga/edición |
| **mercado.market_snapshot** | Estado **vivo** por ticker: precio, book, TEA, duration, paridad | renta_fija, analitica, carry_trade, sensibilidad, fair_value | **motor rofex** (`engines/valores.py`: book + precios) **y motor curvas** (`engines/curvas.py`: TEA/TEM/duration/paridad). Cada uno escribe SOLO sus campos (update parcial) |
| **mercado.snapshots_cierre** | Foto del cierre diario por bono | renta_fija, analitica, carry_trade | **`jobs/snapshot_cierre.py`** (cron 20:25 UTC) |

### 3.2. Tablas DERIVADAS (cálculos a partir de las base)

| Tabla SQL | Qué es | La lee | La llena (verificado) |
|---|---|---|---|
| **mercado.mercado_hist** (`tipo='forwards'`) | Matriz de tasas forward, live y de cierre. ⚠️ **NO existe `mercado.forwards`**: el motor escribe en el histórico genérico y el reader vivo (`mercado_hist_sql.get_forwards`) toma la fila más nueva | derivados.py | **motor forwards** (`engines/forwards.py`) |
| **mercado.forwards_zscore** | Media/desvío por par para z-score | derivados.py | **`jobs/forwards_zscore.py`** (post-cierre) |
| **mercado breakevens** (live) | Breakeven Lecap↔CER (1 fila global, vivo) | derivados.py | **motor breakevens** (`engines/breakevens.py`) |
| **mercado breakevens** (histórico) | Breakevens de cierre (1 fila/fecha) | derivados.py | motor breakevens |
| **mercado.fit_params** | Betas Nelson-Siegel de la curva (fair value) | fair_value.py | **`jobs/fair_value.py`** |
| **mercado.fair_value_residuos** | Residuo/z-score por bono vs curva teórica | fair_value.py | `jobs/fair_value.py` |

> El motor breakevens y el motor forwards **leen `mercado.market_snapshot` +
> `macro.series_macro` (CER)** (y el de breakevens también la inflación mensual de
> `macro.series_macro` y `mercado.timesales`) para calcular. Es decir: si el precio
> vivo no llega, forwards y breakevens no se actualizan. Verificado en
> `engines/breakevens.py:125-205` y `engines/forwards.py:45`.

### 3.3. Tablas de soporte

| Tabla SQL | Qué es | La lee | La llena |
|---|---|---|---|
| **mercado.timesales** | Cada trade (Time & Sales) — alimenta sub-tab Libro | analitica, canje, renta_fija, macro | motor rofex (cada trade) |
| **mercado.canje_cierre** | Cierre diario de tickers de canje | canje.py | `jobs/cierre_canje.py` |
| **macro.series_macro** (CER) | Valor CER publicado por BCRA (1 fila/día) | renta_fija | `jobs/bcra.py` |
| **macro.series_macro** (InflacionMensual) | IPC mensual (usado por motor breakevens) | (motor breakevens) | `jobs/argentina_datos.py` ⚠️ *a verificar el job exacto* |
| **mercado.dias_habiles** | Calendario hábil (liquidación CER T+10) | renta_fija | job de días hábiles ⚠️ *nombre a verificar* |
| **macro.rem** | Consenso de inflación (overlay breakevens / rolldown) | derivados (rem) | `jobs/argentina_datos.py` ⚠️ *a verificar* |
| **macro.uva** | Valor UVA | macro.py | carga manual ⚠️ *a verificar* |
| **mercado.caucion_snapshot** | Caución cierre / vivo | repo.py | motor caución (`engines/caucion.py`) |
| **mercado.futuros_dlr_snapshot** | Futuros DLR cierre / vivo | derivados.py | motor futuros DLR |
| **valuaciones.dolar / valuaciones.dolar_snapshot** | MEP/CCL histórico / vivo | macro, carry_trade | motores dólares / dolar_mep |

---

## 4. Relaciones clave entre tablas (los "joins")

Estos cruces hoy se resuelven **en Python dentro de cada service** (podrían
hacerse como JOINs SQL nativos). Los más importantes (verificados):

1. **El cruce maestro:** `mercado.curvas.instrumento` (el símbolo de mercado; era
   la columna `ticker`) ↔
   `mercado.market_snapshot.ticker` ↔ `mercado.snapshots_cierre.ticker`. Une el
   "DNI" del bono (curvas) con su precio vivo (market_snapshot) o de cierre
   (snapshots_cierre). Aparece en casi todos los endpoints. (`renta_fija.py`,
   `analitica.py`, `sensibilidad.py`.)

2. **CER fijado:** `mercado.curvas.fecha_vencimiento` → `mercado.dias_habiles`
   (T+10) → `macro.series_macro` (CER por fecha). Si el CER de liquidación de un
   bono ya está publicado, el bono "migra" de curva CER a tasa fija en runtime.
   (`renta_fija.py`.)

3. **Par breakeven:** `mercado.curvas` (lecap) ↔ `mercado.curvas` (cer) por
   `fecha_vencimiento` aproximada (±20 días). El motor breakevens empareja Lecap
   con el CER más cercano en plazo. (`engines/breakevens.py`.) Cómo entra un bono
   NUEVO a esa matriz → §4.bis.

### 4.bis Cómo entra un bono NUEVO a BREAKEVENS

**No hay descubrimiento automático de emisiones.** La cadena tiene cuatro
eslabones y el primero es 100% manual:

1. **ALTA MANUAL en `mercado.curvas`** — Manager → TÍTULOS
   (`api/services/bonos_admin.py`, upsert por `ticker`). Nada escanea BYMA
   ni el boletín en busca de licitaciones nuevas: **si nadie carga la Lecap, para
   el sistema no existe.** Este es el eslabón que se desactualiza.
   Campos que el BE necesita: la Lecap/Boncap con `curva='tasa_fija'` +
   `flujo_vencimiento`; el CER con `curva='cer'` + `cer_emision` + `valor_nominal`.
2. **EMPAREJAMIENTO automático** — `engines/breakevens.py::cargar_pares()` cruza
   cada `tasa_fija` con el `cer` de vto más cercano, tolerancia ±20 días
   (`MAX_DIFF_DIAS`). **Un CER solo puede estar en UN par**: si dos Lecaps caen
   sobre el mismo CER gana la de menor diferencia y la otra se descarta sin
   buscarle el segundo CER más cercano. Un bono nuevo puede entonces desplazar a
   otro que venía saliendo.
3. **REINICIO del motor** — `cargar_pares()` corre **una sola vez, al arrancar**
   (fuera del `while True`). El alta NO se ve al instante: se ve cuando el cron
   reinicia `motor_breakevens.service` (13:20 UTC L-V) o con un restart a mano.
   Mismo comportamiento que `motor_rofex`/`motor_curvas` (ver §9.2, falla #7).
4. **CURADURÍA en la lectura** — Manager → TÍTULOS → BREAKEVENS, en los dos
   sentidos y sin tocar el motor:
   - **EXCLUIR** (`mercado.breakevens_overrides`): el motor lo sigue calculando,
     el reader lo oculta. Un par "que no aparece" puede estar apagado ahí.
   - **AGREGAR un par MANUAL** (`mercado.breakevens_manuales`): para los pares que
     el motor nunca arma — los que el dedup descarta y los que caen fuera de los
     ±20 días. El motor no los conoce (carga sus pares al arrancar), así que el BE
     se calcula **en la lectura**, llamando a la MISMA `calcular_breakevens` del
     motor con `min_dias=0` y sin filtro de IPC: los filtros son heurísticas para
     no ensuciar la matriz automática, y un par elegido a dedo no se descarta en
     silencio. Aparece en Renta Fija sin reiniciar nada, marcado `manual: true`.

   Esto es lo que salva el eslabón 3: si necesitás el par HOY, lo agregás a mano
   en vez de esperar al restart del cron.

Dos filtros más recortan la matriz en cada corrida (`calcular_breakevens`):
plazo mínimo **50 días** al vto (`MIN_DIAS_PLAZO`) y `mes_inflacion` (= vto − 2
meses) **posterior** al último IPC publicado — un BE sobre un IPC ya conocido no
es una expectativa, así que se descarta.

> **Diagnóstico:** la mitad DERECHA del panel Manager → TÍTULOS → BREAKEVENS
> (`GET /api/manager/breakevens/diagnostico`) lista cada bono `tasa_fija` del
> master con el motivo exacto por el que entra o no entra, los CER sin par y la
> frescura del doc publicado. Es la forma de distinguir "el motor falla" de "nadie
> dio de alta el bono". `GET /api/manager/breakevens/diagnostico` devuelve lo
> MISMO en consola — las dos leen `breakevens_admin.diagnostico()`, así que la
> pantalla y el script no pueden contradecirse.

4. **Fair value live:** `mercado.fit_params` (betas del cierre) +
   `mercado.market_snapshot` (TEA viva) → `mercado.fair_value_residuos`
   recalculado. (`fair_value.py`.)

5. **Carry / retorno total:** `mercado.snapshots_cierre` (precios) +
   `valuaciones.dolar` (MEP) + `macro.series_macro` (DOLAR oficial A3500).
   (`carry_trade.py`, `renta_fija.py`.)

---

## 5. Modelo SQL de la vista (migración completa)

La migración Mongo→SQL **está completa (2026-06-29)**: la vista lee y escribe
**SQL-native**. No quedan colecciones Mongo ni el batch de espejo `sync_postgres`
como fuente — los motores y jobs escriben directo a Postgres vía `core.pg_mirror`.

### 5.1. Tablas que usa la vista

Verificado contra `sql/schema.sql`. Cada dato (live, cierre, histórico, maestros
y series macro) tiene su tabla SQL y se escribe directo desde su motor/job:

| Tabla SQL | Qué guarda | Quién la escribe |
|---|---|---|
| `mercado.curvas` | Maestro de bonos | carga/edición |
| `mercado.market_snapshot` | Estado vivo por ticker | motor rofex + motor curvas (`core.pg_mirror`) |
| `mercado.snapshots_cierre` | Cierre diario por bono | `jobs/snapshot_cierre.py` |
| `mercado.canje_cierre` | Cierre de tickers de canje | `jobs/cierre_canje.py` |
| `macro.series_macro` | CER · InflacionMensual · BADLAR · DOLAR · TAMAR · RiesgoPais · InflacionInteranual (las 7 juntas) | `jobs/bcra.py`, `jobs/argentina_datos.py` |
| `macro.uva` | Valor UVA | carga manual |
| `macro.rem` | Consenso REM | `jobs/argentina_datos.py` |
| `mercado.mercado_hist` (`tipo='forwards'`) | Forwards live y de cierre | motor forwards |
| `mercado.forwards_zscore` | Coeficientes de z-score | `jobs/forwards_zscore.py` |
| breakevens (live + histórico, schema `mercado`) | Breakevens live y de cierre | motor breakevens |
| `mercado.fit_params` | Betas Nelson-Siegel | `jobs/fair_value.py` |
| `mercado.fair_value_residuos` | Residuo/z-score por bono | `jobs/fair_value.py` |
| `mercado.futuros_dlr_snapshot` | Futuros DLR | motor futuros DLR |
| `mercado.caucion_snapshot` | Caución | motor caución |
| `mercado.timesales` | Trades (Libro) | motor rofex |
| `mercado.dias_habiles` | Calendario hábil | job de días hábiles |
| `valuaciones.dolar` / `valuaciones.dolar_snapshot` / `valuaciones.dolar_oficial_live` | MEP/CCL histórico/vivo + dólar oficial | motores dólares / dolar_mep |

### 5.2. Conclusión para RENTA FIJA

- La vista **funciona 100% sobre SQL**. Lectura por `core.postgres.get_pool()` +
  los services `*_sql.py` / helpers (`core/market_snapshot`, `core/series_macro`);
  escritura SQL-native vía `core.pg_mirror`.
- Tanto el **estado vivo derivado** (matriz live de forwards/breakevens, z-score,
  Time & Sales) como los **históricos y maestros** viven en Postgres.

---

## 6. Cache / frecuencia (verificado: `@cached` en los services)

| Service | TTL de cache |
|---|---|
| `renta_fija.py` (snapshot bonos) | 10s (+ otros: 15/30/60/300) |
| `derivados.py` (forwards/breakevens) | forwards/breakevens 5–30s; históricos 300s |
| `fair_value.py` | 30s (live) / 300s (cierre, histórico) |
| `analitica.py` | 60–300s |
| `macro.py` (series BCRA) | 3600s; MEP 5s |
| `repo.py` (caución) | 5s (live) / 300s (histórico) |

El front poll de la pantalla: `snapshot-live` cada 5s; fair-value live 90s;
forwards-zscore 5 min; trades (Libro) 5s. *(Intervalos según el código del
front; el backend manda con su TTL de cache.)*

---

## 7. ⚠️ Pendiente de medir en prod (NO asumido)

Esto **no se puede afirmar leyendo código** — requiere correr una medición:

1. **Conteos reales por tabla** — no hay ningún número en este doc a propósito
   (sería fruta). Salen de una consulta read-only sobre Postgres.

2. **Nombres de job exactos** marcados `⚠️ a verificar` en §3.3
   (InflacionMensual / DiasHabiles / REM / UVA): sé qué tabla es y que la leen,
   pero no confirmé el job que las escribe leyendo su línea. No afecta la vista
   (son soporte), pero queda anotado para no afirmar de más.

---

## 8. Archivos fuente (para regenerar/auditar este doc)

- **Frontend:** `acaquant-web/src/app/renta-fija/page.tsx` + componentes
  `renta-fija-live.tsx`, `renta-fija-table.tsx`, `forwards-panel.tsx`,
  `curvas-chart.tsx`, `breakevens-block.tsx`, `fair-value-view.tsx`,
  `libro-panel.tsx`.
- **Routers:** `api/routers/cotizaciones.py`, `api/routers/analitica.py`.
- **Services:** `renta_fija.py`, `derivados.py`, `fair_value.py`, `analitica.py`,
  `canje.py`, `carry_trade.py`, `sensibilidad.py`, `simular_inversion.py`,
  `titulos_flujos.py`, `macro.py`, `repo.py`, `rem.py`.
- **Motores:** `engines/valores.py`, `engines/curvas.py`, `engines/forwards.py`,
  `engines/breakevens.py`, `engines/caucion.py`.
- **Jobs:** `snapshot_cierre.py`, `cierre_canje.py`, `bcra.py`, `fair_value.py`,
  `forwards_zscore.py`, `argentina_datos.py`.
- **SQL:** conexión `core.postgres.get_pool()`; escritura `core.pg_mirror`;
  helpers de lectura `core/market_snapshot`, `core/series_macro`; schema
  `sql/schema.sql` (tablas `mercado.curvas`,
  `mercado.market_snapshot`, `mercado.snapshots_cierre`, `mercado.canje_cierre`,
  `mercado.forwards_zscore`, `mercado.fit_params`,
  `mercado.fair_value_residuos`, `mercado.futuros_dlr_snapshot`,
  `mercado.caucion_snapshot`, `mercado.timesales`, `mercado.dias_habiles`,
  `macro.series_macro`, `macro.rem`, `macro.uva`, `valuaciones.dolar`).

---

## 9. Salud de la valuación — las dos patas y el catálogo de fallas

> Vivía en `docs/SALUD_CURVAS.md`, que se borró el 2026-08-31. Ese doc era un
> **roadmap hacia un health-check** («que el sistema avise solo en vez de
> descubrir los errores a ojo») y ese roadmap **ya se construyó**: es el AV AGENT
> (`docs/AGENT.md`). Su §7 nombraba `jobs/curvas_healthcheck.py` y
> `manager.controles_datos`, que no existen; su §2 describía las columnas de
> `mercado.curvas` con la semántica **anterior** al renombre del 2026-08-15
> (decía que `ticker` era el símbolo de mercado — hoy eso es `instrumento`, ver
> el `CLAUDE.md` raíz). Sobrevive lo que sigue siendo cierto y no está en otro
> lado: **el problema de la pata** y el **catálogo de fallas**.

### 9.1 Las dos patas (ARS / USD) — la causa raíz más sutil

Casi toda ON cotiza en **dos patas**: una en **pesos** (`VSCIO`, precio ~144.000)
y una en **dólares** (sufijo `D`, `VSCIOD`, precio ~100). `mercado.curvas` guarda
**una sola** en `instrumento` → de ahí sale el precio que valúa. La regla:

- **HD (hard-dollar)** → la **pata USD** (precio ~100, tal cual). Con la pata
  peso el motor hace ÷MEP y el MEP **no siempre recupera el precio dólar real**
  → paridad y TEA infladas. *(`YMCTO`: pata peso 160.000 ÷MEP = 110 → TEA −25%.)*
- **DL (dólar-linked)** → la **pata peso** (~144.000, ÷A3500). Con la pata USD
  (~100) el motor la detecta por escala (<1000) y la usa tal cual. *(`TTCEO`.)*
- **ARS (peso nativo)** → pata peso, precio directo.

**Esto ya no es un frente abierto sin dueño**: lo mira la habilidad
`precio_moneda` del agente (`agente/catalogo.py`), que separa las dos causas y
**tiene botón para las dos** — `pata_equivocada → apuntar_pata` y
`cotiza_en_pesos → pata_dolar`. Sus umbrales son la paridad fuera de `[40, 160]`,
que es exactamente la regla que este doc proponía escribir a mano.

### 9.2 Catálogo de fallas — síntoma → causa → quién lo ve hoy

| # | Síntoma | Causa | Quién lo detecta HOY |
|---|---|---|---|
| 1 | TEA `--` o falsa | `moneda_flujo` ≠ tipo real (CARTERA) | `bono_sin_tasa` (rueda) + `tasas_al_cierre` (17:30 ART) |
| 2 | XIRR no converge | flujo en escala peso vs precio USD (o al revés) | `bono_sin_tasa`; el detalle, con DEBUG TEA |
| 3 | Paridad explotada (ej. 144.500%) | `valor_residual` en otra escala que el flujo | `precio_moneda` (umbral `paridad_max=160`) |
| 4 | TEA negativa/inflada en un HD | **pata peso** guardada en un HD → ÷MEP infla el precio | `precio_moneda · pata_equivocada` → botón `apuntar_pata` |
| 5 | TEA absurda (>50% o <−50%) | precio stale/ilíquido, o bono distressed | `bono_sin_precio · precio_viejo` (**sin arreglo a propósito**: es un dato del papel, no del sistema) |
| 6 | Bono sin TEA pero con precio | precio de pantalla sin trade (market data) | `bono_sin_precio · sin_punta` (aviso, sin arreglo) |
| 7 | Bono no cotiza tras un alta o un cambio | **los motores cargan `mercado.curvas` una sola vez al arrancar** | `bono_sin_precio · no_suscripto` → botón `pedir_pata`; si no, `systemctl try-restart motor_rofex motor_curvas` **fuera de rueda** |
| 8 | Bono cargado que no valúa nada | sin cronograma de pagos | `bono_sin_flujo` → botón `alta_flujos` |
| 9 | El bono existe en 1816 y no en el master | alta pendiente | `soberanos_faltantes` → botón `alta_bono` |

⚠️ **La falla #7 es la que más se paga y la única que no se arregla con un dato**:
un bono nuevo o un cambio de `moneda_flujo`/`flujos` **no impacta hasta reiniciar**
los motores, y el deploy **no los reinicia a propósito** (ver `CLAUDE.md` §Deploy).

### 9.3 La herramienta para mirar UN bono

**DEBUG TEA** (`/manager → checks`, `api/services/debug_curva.py`) replica el
motor para un ticker: precio, TC usado, flujos, el cashflow del XIRR, y compara
lo calculado contra lo persistido. Lee el precio de **`mercado.market_snapshot`**
(la misma fuente que el motor), no de `mercado.timesales` — por eso muestra bonos
que cotizan sin haber operado.
