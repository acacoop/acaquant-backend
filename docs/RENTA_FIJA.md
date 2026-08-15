
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
| 1 | Medir por qué tarda (`scripts/diag_renta_fija_perf.py`) | no | ✅ **medido** |
| 2 | Clasificar los 222 con los ejes (`scripts/clasificar_curvas.py`) | no | ✅ **aplicado** (212/222, test VERDE) |
| 3a | Endpoint `GET /api/cotizaciones/curvas-vista` (nadie lo consume) | no | **hecho** |
| 3b | Tab **CURVAS** en el front (ARS izq / USD der) + absorber ONs | sí | pendiente |
| 4 | Tab **FORWARDS** (+ Fair Value adentro) | sí | pendiente |
| 5 | Job de 1816 → altas automáticas (`docs/VISTA_RESEARCH.md` §4.10) | no | pendiente |
| 6 | Renombrar las columnas de `mercado.curvas` | no | ✅ **hecho** |
| 7 | Migrar el blob `data` (y matarlo) + ficha única en `assets` | no | pendiente |
| 8 | `mercado.especies` — las PATAS de cada bono | no | ✅ **aplicado** (758 patas) |
| 9 | Limpiar el VALOR de `curvas.ticker` (sacar el sufijo D/C) | sí | pendiente |

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

**GUARDRAIL — `especies_cruzadas`** (`jobs/guardrails.py`, corre 20:45 UTC L-V).
El cruce de `AO29` estuvo meses invisible: el dato estaba, pero **ninguna pantalla
se hacía la pregunta**. El check la hace todos los días y cuenta los bonos cuyo
instrumento default no es de la moneda en la que se denominan **habiendo pata
disponible** (esa segunda mitad es la que evita el falso positivo de las ONs sin
pata en dólares). Nace **sin calibrar** a propósito — `especies_cruzadas_max: None`
en `config.GUARDRAILS_UMBRALES` — así reporta el número medido sin marcar rojo
mientras los 34 de las ONs siguen abiertos; se fija en **0** cuando el rediseño de
ONs los cierre.

**Estado (2026-08-15, aplicado):** 758 patas sembradas · `AO29` y `CO32`
repuntados a su pata en dólares · **34 cruces pendientes, todos ONs**.

⚠️ **Lo que TODAVÍA no se hizo: limpiar el valor de `curvas.ticker`.** El renombre
del paso 6 cambió los NOMBRES de las columnas, no los VALORES: 17 bonos siguen
teniendo `AL30D` como PK, y por eso la vista sigue mostrando `AL30D` en la columna
TICKER. Ahora sacar el sufijo **es seguro** —`mercado.especies.ticker_especie`
guarda la identidad de la pata, así que la D ya no se pierde— pero es un paso
propio (paso 9): toca la PK y hay que mover con ella los 4 `portafolio.assets` que
la arrastran.

Siembra: `python -m scripts.sembrar_especies` (DRY-RUN) / `--aplicar`. Nadie lee
la tabla todavía: se puebla, se mira, y recién después se mudan los readers.

### Paso 6 — los nombres de `mercado.curvas` (2026-08-15)

`ticker_corto` (la PK) **era** el ticker del bono y `ticker` **era** el símbolo de
mercado. Quedó al derecho:

| antes | ahora | qué es |
|---|---|---|
| `ticker_corto` (PK) | **`ticker`** | `AL30` — joinea con `portafolio.assets.ticker` |
| `ticker` | **`instrumento`** | `MERV - XMEV - AL30 - 24hs` — el símbolo que se le manda a Primary |
| `instrumento` (eje bono/letra) | **`tipo_instrumento`** | tuvo que liberar el nombre |

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

**Medido antes de tocar** (`scripts/diag_activos.py`, 221 instrumentos):

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
1816 — ver `docs/VISTA_RESEARCH.md` §4.10):

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
termina el más lento. Corrida real (`scripts/diag_renta_fija_perf.py`):

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
> El diseño para automatizar eso vive en **`docs/VISTA_RESEARCH.md` §4.10** —
> ahí están los números medidos (212 de nuestros 222 bonos están en 1816, 98,6%
> de cobertura de cashflow, ~29 créditos/día detectar novedades) y la decisión
> asentada: **el job PROPONE el alta, no la escribe solo** (un flujo mal escalado
> entra directo a la valuación y al AuM). Herramienta: `python -m
> scripts.diag_1816_mapeo`. Antes de tocar `mercado.curvas.curva` —el campo que
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
| **mercado.forwards** (live) | Matriz de tasas forward (1 fila/curva, vivo) | derivados.py | **motor forwards** (`engines/forwards.py`) |
| **mercado.forwards** (histórico) | Forwards de cierre (1 fila/fecha,curva) | derivados.py | motor forwards |
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
| **macro.series_macro** (CER) | Valor CER publicado por BCRA (1 fila/día) | renta_fija, descomposicion_retorno | `jobs/bcra.py` |
| **macro.series_macro** (InflacionMensual) | IPC mensual (usado por motor breakevens) | (motor breakevens) | `jobs/argentina_datos.py` ⚠️ *a verificar el job exacto* |
| **mercado.dias_habiles** | Calendario hábil (liquidación CER T+10) | renta_fija | job de días hábiles ⚠️ *nombre a verificar* |
| **macro.rem** | Consenso de inflación (overlay breakevens / rolldown) | derivados (rem), descomposicion_retorno | `jobs/argentina_datos.py` ⚠️ *a verificar* |
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
   Mismo comportamiento que `motor_rofex`/`motor_curvas` (ver `SALUD_CURVAS.md` §6 #7).
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
> dio de alta el bono". `python -m scripts.diag_breakevens_cobertura` imprime lo
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
| `mercado.bonds_master` | Maestro complementario de bonos | carga/edición |
| `mercado.market_snapshot` | Estado vivo por ticker | motor rofex + motor curvas (`core.pg_mirror`) |
| `mercado.snapshots_cierre` | Cierre diario por bono | `jobs/snapshot_cierre.py` |
| `mercado.canje_cierre` | Cierre de tickers de canje | `jobs/cierre_canje.py` |
| `macro.series_macro` | CER · InflacionMensual · BADLAR · DOLAR · TAMAR · RiesgoPais · InflacionInteranual (las 7 juntas) | `jobs/bcra.py`, `jobs/argentina_datos.py` |
| `macro.uva` | Valor UVA | carga manual |
| `macro.rem` | Consenso REM | `jobs/argentina_datos.py` |
| `mercado.forwards` (+ histórico) | Forwards live y de cierre | motor forwards |
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
  `canje.py`, `carry_trade.py`, `sensibilidad.py`, `descomposicion_retorno.py`,
  `titulos_flujos.py`, `macro.py`, `repo.py`, `rem.py`.
- **Motores:** `engines/valores.py`, `engines/curvas.py`, `engines/forwards.py`,
  `engines/breakevens.py`, `engines/caucion.py`.
- **Jobs:** `snapshot_cierre.py`, `cierre_canje.py`, `bcra.py`, `fair_value.py`,
  `forwards_zscore.py`, `argentina_datos.py`.
- **SQL:** conexión `core.postgres.get_pool()`; escritura `core.pg_mirror`;
  helpers de lectura `core/market_snapshot`, `core/series_macro`; schema
  `sql/schema.sql` (tablas `mercado.curvas`, `mercado.bonds_master`,
  `mercado.market_snapshot`, `mercado.snapshots_cierre`, `mercado.canje_cierre`,
  `mercado.forwards`, `mercado.forwards_zscore`, `mercado.fit_params`,
  `mercado.fair_value_residuos`, `mercado.futuros_dlr_snapshot`,
  `mercado.caucion_snapshot`, `mercado.timesales`, `mercado.dias_habiles`,
  `macro.series_macro`, `macro.rem`, `macro.uva`, `valuaciones.dolar`).
