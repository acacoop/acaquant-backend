# BYMA CUSTODIA (CVSA) — **[VIVO]**

Lo que la **Caja de Valores** tiene registrado a nombre nuestro. Doc madre:
`docs/ARQUITECTURA.md`. Cliente: `core/byma_custodia.py`. Vista: `/back-office`
→ tab **CUSTODIA**.

> **Todo lo de acá está MEDIDO contra producción, no leído del OpenAPI.** El
> spec que publica BYMA omite o contradice casi todo lo que importa, y cada
> renglón marcado ⚠️ es algo que costó un error en pantalla antes de entenderse.

---

## 0. Qué es, y qué NO es

| | Aunesa | BYMA / CVSA |
|---|---|---|
| Qué es | El back-office tercerizado | El **registro**: lo que la Caja tiene anotado |
| Nos da | Tenencia valorizada, saldos, informes | Tenencia en nominales, por estado |
| Quién manda | — | **CVSA**: cuando difieren, la razón legal es de la Caja |

No son intercambiables: CVSA es la posición **registral liquidada**; Aunesa
(`tenencia_live`) es una posición **proyectada**. Mezclarlas sería otro dato
partido (`core/duplicados.py`).

---

## 1. Autenticación

```
POST https://api.byma.com.ar/oauth/token/
Content-Type: application/x-www-form-urlencoded
client_id / client_secret / grant_type=client_credentials / scope
```

- **El token dura 86.400 s (24 h).** Se cachea por scope con 10 min de margen.
- Dos scopes, uno por API: `custodysecurities.read` · `custodyfees.read`.
- Env: `BYMA_CLIENT_ID`, `BYMA_CLIENT_SECRET`, `BYMA_PARTICIPANT_CODE` (= `74`).
- IdP: **Okta** (el `client_id` empieza con `0oa` — cero, no letra O).

⚠️ **Las credenciales las genera el portal, no se transcriben.** En el método →
*Autenticación* → *"¿Cómo obtener un token de acceso?"* → elegir la aplicación
en el desplegable: ahí aparecen los cuatro valores. Un carácter mal copiado da
un 400 cuyo cuerpo dice 401 (el gateway envuelve el error de su IdP), y ese
error es indistinguible de una credencial revocada.

Ambientes (los dos públicos; los `*.vortex.byma.com.ar` del OpenAPI **no existen
en el DNS de internet**, son internos de BYMA):

| | Host |
|---|---|
| Producción | `api.byma.com.ar` · `200.42.14.174` |
| Homologación | `hs-api.byma.com.ar` · `200.42.14.176` |

Solo los métodos **POST/PUT** de Custodia y Clearing requieren pasar por
homologación. Los GET van directo a producción.

---

## 2. Los métodos

### Custody Securities v1 — `api.byma.com.ar/custody-securities/v1`

| Método | Params | Modo | Formato | Techo |
|---|---|---|---|---|
| `GET /holdings` | `balanceDate`, `participantCode` | **asíncrono** | CSV | 100/s · 10.000/día |
| `GET /holdings/accounts` | `accountNumber`, `participantCode`, `subBalanceType?` | inmediato | JSON | ídem |
| `GET /transactions` | `settlementDate`, `participantCode` | **asíncrono** | CSV (9 col) | ídem |
| `GET /transactions/today.csv/` | `participantCode` | **asíncrono** | CSV (11 col) | ⚠️ **2/min · 100/día** |
| `POST /transactionsbyreference.csv` | `instructionReferences`, `participantCode`, `settlementDate?` | **asíncrono** | CSV (13 col) | ⚠️ **100/min · 1000/día** |

⚠️ **Tres cosas que el PORTAL dice mal** y la documentación de BYMA corrige. Las
tres están en el código como son, no como el portal las declara:

1. **`/transactions/today` NO es inmediato**: hace el mismo baile del uuid, y el
   `.csv` va ADENTRO del path, con barra final. La primera versión de este
   módulo lo implementó como sincrónico y habría muerto en el primer 202.
2. **`POST /transactionsbyreference` LEE, no escribe.** La ficha dice *"ejecuta
   una tarea de escritura… el origen expuesto se verá afectado"*: es el
   boilerplate que el gateway le pone a TODO POST. BYMA lo define como
   *"consultar los movimientos asociados a las instrucciones de custodia…
   filtrando en función de su `instructionReference`"*. Es un GET con el filtro
   en el cuerpo, porque una lista de N referencias no entra en una query string.
3. **`currency` es un CÓDIGO**, no el "nombre completo de la moneda" que dice el
   diccionario: `0` = ARS, `1` = USD, `2` = USD-Trf (`byma_custodia.MONEDAS`).

**Los tres métodos comparten las 9 primeras columnas** y se diferencian por lo
que agregan: `today` suma `COUNTERPARTY` y `COUNTERPARTYSECURITIESACC`; el POST
suma además `SETTLEMENTSTATUS` y `SETTLEMENTSTATUSREASON` — el estado de
liquidación, que **ningún método masivo devuelve**, y es la única razón por la
que el POST existe en el sistema. Por eso el parseo es por NOMBRE y la escritura
usa `COALESCE`: lo que un método no trae no puede pisar lo que otro ya escribió.

⚠️ **La cabecera del CSV cambia de capitalización según el método**:
`/holdings` la manda en camel (`participantCode`) y `today` en mayúscula
sostenida (`PARTICIPANTCODE`). La detección compara en minúscula — si no, la
cabecera de `today` entra como una fila de datos y aparece un movimiento
fantasma con volumen `None`. No falla nada: solo queda mal. Congelado por test.

### ⚠️ PARTIDA DOBLE — lo que define el modelo de movimientos

Cada `instructionReference` viene **dos veces**, con `volume` de signo opuesto,
una por cada cuenta que participa. Medido contra producción:

```
6/3        →  -280958.5138   SUSC20260410272
6/600613   →  +280958.5138   SUSC20260410272
```

No son dos movimientos: es **uno con dos patas**. De ahí salen dos decisiones:

- **`instructionReference` NO puede ser la PK**, y las cinco columnas **hacen
  falta**. Medido sobre el primer lote real (116 filas, 87 referencias):

  | Clave candidata | Combinaciones | |
  |---|---|---|
  | `referencia` | 87 | pierde 29 filas |
  | `referencia + cuenta` | 106 | pierde 10 |
  | `referencia + cuenta + instrumento` | 106 | el papel no aporta nada |
  | `+ sub_balance_type` | **116** | ✅ la única que conserva todo |

  O sea: hay **10 filas donde la misma cuenta liquida el mismo papel repartido en
  dos sub-balances** (parte disponible, parte trabado) — igual que en tenencias.
  Angostar la clave por el camino que parecía obvio (`referencia + cuenta`)
  habría borrado esas 10 en silencio. El ingest sigue midiendo cada lote
  (`custodia_escritura.contar_claves`, campo `claves` de la respuesta): si
  apareciera una combinación que ni esta clave separa, se ve en el acto.

- **Un movimiento puede tener MÁS DE DOS PATAS** — consecuencia directa de lo
  anterior, y la primera versión del plegado lo hacía mal: tomaba el volumen de
  la PRIMERA pata, así que esos 10 movimientos mostraban un nominal **parcial**,
  más chico que el real, sin que nada fallara. Ahora el nominal se **acumula por
  lado** (`_entra` / `_sale`) y el mayor de los dos es el movimiento. Si de un
  lado hubiera dos cuentas distintas, la pantalla dice `"N cuentas"` en vez de
  elegir una al azar. Congelado por test.
- **La tabla guarda patas; la pantalla muestra movimientos.** El plegado (dos
  patas → una fila `entrega → recibe`) lo hace `custodia_sql._plegar`, en el
  backend: el front de esta app no deriva ni suma nada. Un movimiento contra un
  agente distinto tiene UNA sola pata nuestra y eso **no es un descalce** — se
  cuenta aparte (`sin_par`) para que la pantalla lo diga tal cual.

### El pipeline de movimientos: quién alimenta y quién repara

| Método | Rol | Por qué |
|---|---|---|
| `/transactions/today` | el **feed** | es el que eligió la mesa; corre desde la PC |
| `/transactions` | el **reparador** | `today` NO es re-ejecutable: un día que no corra es un día perdido. Con `settlementDate` se vuelve a bajar |
| `POST …byreference` | el **detalle** | único con `settlementStatus`. Un click, una llamada — **nunca** en bucle por fila |

Los tres escriben por la MISMA función (`custodia_escritura.guardar_movimientos`).

⚠️ **UPSERT, no DELETE+INSERT** — al revés que `custodia_cvsa`. Las tenencias son
una FOTO (lo que ya no está tiene que desaparecer); los movimientos son HECHOS
(no dejan de haber pasado). Borrar por ausencia perdería historia que CVSA purga
a los 7 días y no se puede reconstruir.

⚠️ **El detalle por referencia está BLOQUEADO hasta el whitelist de IP.** Es el
único de los tres que no se puede alimentar por el script de la PC: un feed
empuja datos, pero un click necesita que el backend salga a BYMA en ese momento
—y el Droplet no llega—. Las columnas `estado`/`estado_motivo` ya están en la
tabla para que el día que se prenda no haya migración.

⚠️ **Hipótesis (sin medir)**: el cuerpo del POST va como JSON con
`instructionReferences`. Es lo que declara el OpenAPI; nadie lo corrió contra
producción todavía. Si contesta 400, el candidato siguiente es form-urlencoded
con el mismo nombre de campo.

⚠️ **`balanceDate` solo acepta los últimos 7 días** — más atrás CVSA lo purgó.
Por eso el histórico **no se puede reconstruir a pedido**: o se guarda día a día,
o se perdió.

### Custody Fees v2 — `api.byma.com.ar/custody-fees/v2`

Cinco métodos (`/daily-fees/{equity,fixed-income}`, `/monthly-fees/{equity,
fixed-income,nsi}`). **Sin integrar.** El OpenAPI declara cero parámetros; el
propio endpoint reveló que falta **`accountgroupcode`**, cuyo valor todavía no
tenemos. Cuando esté: es la única fuente que trae `cvsaIdentifier` **e** `isin`
en la misma fila, o sea la tabla de traducción, además del costo de custodia
—que hoy no está modelado en ningún lado del sistema—.

---

## 3. ⚠️ Las ocho trampas del gateway

Ninguna está en el OpenAPI. Las ocho viven resueltas en `core/byma_custodia.py`.

1. **`Accept: application/json` da HTTP 406.** Cada método elige su formato y el
   gateway no negocia. Se manda `Accept: */*` y se parsea lo que venga.

2. **Los métodos asíncronos contestan HTTP `202` con un `"code": 409` ADENTRO
   del cuerpo.** El 409 es un campo del JSON, no el status — quien mire el status
   buscando un 409 no matchea nunca y el job muere en el primer paso. Por eso el
   trabajo pendiente **se reconoce por el `uuid`**, no por el código:

   ```
   HTTP 202  {"message": "Response is not ready call later with uuid >> ...",
              "code": 409, "uuid": "1469bb7e-..."}
   ```

   Hay que repetir la llamada con la cabecera **`X-UUID`** hasta que conteste 200.
   El cliente lo hace con backoff, **tope de intentos y timeout**: un poll sin
   techo no falla nunca, simplemente no termina, y nadie se entera.

3. **`meta.count` MIENTE.** Medido: `"count": 3` sobre un `result` de **cuatro**
   filas. No se puede usar para paginar ni para validar. Se cuenta
   `len(result)`; si difieren queda un `warning` — el día que lo arreglen
   queremos saberlo, porque hoy ignoramos un campo a propósito.

4. **El JSON viene envuelto** en `{"meta": {...}, "result": [...]}`. El OpenAPI
   declara un objeto plano.

5. **El CSV se separa con `;`**, sin cabecera garantizada, y trae un campo que no
   figura en ninguna documentación: **`identAccountComposite`** (el id interno y
   estable de la cuenta en CVSA).

6. **Hay sufijos de formato en el path** (`.json`, `.csv`, `.dict`, `.swagger`)
   que el OpenAPI no menciona. `.swagger` devuelve la spec real del método —
   aunque en Custody Fees contesta 500. **`/transactions/today.csv/` lo lleva
   obligatorio**, con barra final.

7. **`/transactions/today` es ASÍNCRONO**, aunque el portal diga que "la
   respuesta será inmediata". Hace el mismo baile del `uuid` que `/holdings`.
   Implementarlo como sincrónico —que es lo que decía el spec— lo mata en el
   primer 202.

8. **La cabecera del CSV cambia de capitalización según el método**:
   `participantCode` en `/holdings`, `PARTICIPANTCODE` en `today`. La detección
   compara en minúscula; comparando tal cual, la cabecera entra como fila de
   datos y aparece un movimiento fantasma con volumen `None` — sin que falle
   nada. Y **`currency` es un CÓDIGO** (`0`/`1`/`2`), no el "nombre completo de
   la moneda" del diccionario.

---

## 4. ⚠️ Identidad — REGLA #9

### ⚠️ LA CUENTA ES UN PAR, NO UN NÚMERO — los TRES espacios de CVSA

CVSA usa **tres espacios de numeración para el MISMO agente**, y el número de la
derecha **se repite entre ellos**. Declarados en `core/custodia_cuentas.py`:

| Prefijo | Espacio | Qué es |
|---|---|---|
| `74` | comitentes | las cuentas de clientes — el único cruzable con `clientes.cuentas`, **menos las declaradas** |
| `70074` | liquidadoras | por donde pasan los títulos al liquidar |
| `80074` | garantías | lo afectado a garantía en la cámara |

Las que tienen nombre propio (`ESPECIALES`, las pasó la mesa desde la ficha):

```
74/3             Cuotapartes FCI Bilaterales
74/111111111     Cuotapartes FCI Bilaterales
70074/10000      Cta. Liquidadora gral.
70074/50000      Cta. Liquidadora Licis
80074/555555555  Cta. Gtías. Clientes
80074/222222222  Cta. Gtías. House
80074/888888888  Cta. Gtías. Default funds
```

**Guardar solo el lado derecho era un bug de REGLA #9 en su forma más cara.**
`80074/555555555` y `74/555555555` colapsaban al mismo `"555555555"`, y eso
rompía en tres lugares a la vez, ninguno de los cuales fallaba:

1. **La PK**: las dos filas se pisaban. Una desaparecía y nadie se enteraba.
2. **El join a `clientes.cuentas`**: la Cta. Gtías. Clientes mostraba el nombre
   de un comitente que no tiene nada que ver con ella.
3. **La comparación contra Aunesa**: se restaba la tenencia de un cliente contra
   el saldo de la cámara, y la diferencia salía en rojo por una razón inventada.

⚠️ **Y el espacio NO alcanza para saber si una cuenta es un cliente.** Dentro
del espacio `74` hay cuentas que TAMPOCO son comitentes: `74/3` y `74/111111111`
son de **cuotapartes de FCI Bilaterales**. Están declaradas en `ESPECIALES` y
por eso quedan fuera del cruce — sin eso, `74/3` traería el nombre del comitente
`3`, si existe, y sería el mismo bug con otra ropa. La regla es: **una cuenta es
comitente si su espacio lo es Y no está declarada.** La declaración siempre gana.

Por eso `participante` es una **columna y parte de la PK** de las dos tablas, y
los joins con comitentes van **filtrados por espacio Y por la lista de
declaradas** (`no_comitentes_del_espacio()`, porque el SQL no puede llamar a
`es_comitente()` fila por fila). Un prefijo
que no esté declarado devuelve `espacio = None` y la pantalla lo marca en ámbar:
si CVSA agrega un espacio nuevo queremos verlo, no que el código lo clasifique
de prepo con una regla de strings. Congelado por seis tests.

⚠️ **`/holdings` devuelve SOLO el participante que se le pide**, así que el feed
los pide los tres — y los manda **en un solo envío**: el endpoint reemplaza la
foto del día entera, de modo que tres envíos separados harían que el último
borre a los dos anteriores. Sin filas faltantes visibles, solo cuentas que
desaparecen.

### La cuenta: se PARTE, no se interpreta

```
accountNumber = "74/805"
                 │   └── nuestro clientes.cuentas.id_cuenta
                 └────── participantCode (74)
```

`core.byma_custodia.id_cuenta()` es el único lugar que conoce esa forma.
Devuelve `None` si el formato no es el esperado: un formato distinto es algo que
hay que mirar, no algo que se adivina.

### La especie: NO hay pareo por nombre

`cvsaIdentifier` es un **número interno de CVSA** (`5921`, `9422`, `58790`). No
se parece a ningún ticker nuestro. El puente es **`portafolio.assets.codigo_cnv`**.

⚠️ **`codigo_cnv` se llama así por historia pero guarda el código de CVSA.**
Verificado sobre **254 assets** que ya lo tenían cargado a mano: **cero
divergencias** contra el maestro de BYMA. No se renombra la columna (la usan el
panel Manager → ASSETS, `jobs/assets_autofill` y `api/services/assets_sql`), pero
el nombre miente y hay que saberlo.

**El código es por INSTRUMENTO, no por especie ni por plazo.** Medido sobre el
maestro (17.125 filas → 6.362 tickers):

```
AL30 (ARS) · AL30C (EXT) · AL30D (USD)  →  todos cvsa 5921, isin ARARGE3209S6
```

Por eso alcanza **una columna en `assets`** y no hay que tocar `mercado.especies`.
Y por eso **`codigo_cnv` no lleva `UNIQUE`**: 6.362 tickers → 1.725 códigos.

El maestro vive en `scripts/data/cvsa_especies.csv` y se carga con
`python -m scripts.backfill_codigo_cvsa` (dry-run por default; nunca pisa un
valor cargado; lo que no está en el maestro no se toca, así que los FCI quedan
intactos). BYMA va a dar una API para mantenerlo — ahí el CSV se reemplaza por
una llamada y el script queda igual.

---

## 5. `subBalanceType` — lo que Aunesa no nos da

Quince estados. El que más importa: **qué parte de la tenencia NO se puede
entregar ni garantizar**, información que hoy no existe en el sistema.

```
AVAILABLE · RESERVED · PENDING_DELIVERY · BLOCKED · BLOCKED_FOR_PLEDGE
BLOCKED_FOR_CA · CCP_RESERVED · CCP_RESTRICTED · EMBARGO · INTERIM
PENDING_REDEMPTION · ATTEND_GENERAL_MEETING · PROOF_OF_OWNERSHIP
RESERVED_FOR_PLEDGE · BLOCKED_COLLATERAL
```

Una cuenta puede tener el mismo papel en dos estados a la vez: por eso el estado
es parte de la PK de `portafolio.custodia_cvsa`.

---

## 6. ⚠️ EL CAMINO DE RED — por qué el feed corre en la PC, no en el Droplet

**Las APIs de BYMA no están publicadas en internet abierta.** Están detrás de
**AppGate SDP**, el mismo túnel por el que se entra al portal (en AppGate figuran
como aplicaciones `CUSTODIA` y `CUSTODIA QA`).

Medido el 2026-09-12, y la prueba es inequívoca:

| Desde | AppGate | IP pública | Resultado |
|---|---|---|---|
| PC oficina | **ON** | 186.22.18.181 | **conecta** |
| PC oficina | **OFF** | 186.22.18.181 | **timeout** |
| Mac personal | (no tiene) | otra | timeout |
| Droplet | (no tiene) | 157.230.211.57 | timeout |

**La misma IP pública da resultados opuestos según el túnel.** Eso descarta un
filtro por IP: AppGate no cambia la salida a internet, tunelea ciertos destinos.

Y no es un problema del Droplet: `scripts/diag_byma_red.py` confirma que desde
ahí Aunesa, BCRA y Finnhub conectan sin problema, con `ufw` inactivo y política
`OUTPUT ACCEPT`.

### Cómo entra la tenencia hoy

```
PC con Okta ──AppGate──> BYMA /holdings
     │
     └── POST /api/ingest/custodia/holdings  (X-Ingest-Token + CF Access)
              └──> core/custodia_escritura.guardar() ──> portafolio.custodia_cvsa
```

Mismo patrón que **MAE** (dólar oficial) y **Eikon/Refinitiv**: la oficina tiene
el acceso, el servidor tiene la lógica, y el puente es un POST con token
dedicado. Ver `docs/SECURITY.md`.

- **En la PC**: `scripts/byma_feed.py` (+ `byma_feed.bat`). Standalone —solo
  stdlib— para no copiar el repo. **No interpreta nada**: pega y reenvía crudo.
- **En el server**: `core/custodia_escritura.guardar()`, con las guardas.

### El día que habiliten la IP

`jobs/custodia_cvsa.py` y las dos líneas del crontab **están escritas y
comentadas**, listas. Descomentar y devolverle la `unidad` a su Pieza en
`api/services/diagnostico_registry.py` (un test cruza el crontab con ese árbol).

**No hay nada que migrar**: el job y el endpoint de ingesta llaman a la MISMA
función de escritura. Si cada uno tuviera su copia, el día del cambio tendríamos
dos escrituras capaces de divergir sin que falle nada.

Pedido pendiente a BYMA: habilitar `157.230.211.57`, o provisionar un cliente
headless de AppGate para el servidor.

---

## 7. La comparación contra AUNESA (T0)

La vista cruza cada fila de la Caja contra `portafolio.tenencia_live` con
`horizonte = 't0'` — la posición **liquidada a HOY**, que el código define como
*"lo que está en custodia y se puede entregar, garantizar o caucionar"*.

### Las cuatro reglas

**① El universo lo define BYMA.** Se parte de lo que trae la Caja y se le busca
su contraparte en Aunesa, nunca al revés. En Hygirus hay mucho que no está en
CVSA (FCI, entre otros) y arrastrarlo sería llenar la pantalla de descalces que
no son descalces. La Caja es la fuente de verdad; lo que ella no registra, esta
vista no lo discute.

**② El grano es (cuenta, papel).** CVSA informa una fila por `sub_balance_type`
—el mismo papel puede estar parte `AVAILABLE` y parte `EMBARGO`—; Aunesa informa
una sola. Se **suman los estados** de CVSA antes de comparar: sin eso, el valor
de Aunesa se repetiría en cada fila y la diferencia daría mal en todas.

**③ ⚠️ `VN AUNESA` NO es `cantidad` a secas.**

```
VN AUNESA = cantidad − gar_cantidad        (gar_cantidad NULL → cantidad tal cual)
DIFERENCIA = VN BYMA − VN AUNESA
```

BYMA informa la tenencia **sin lo afectado en garantía**. Comparar contra el
total de Aunesa marcaría en rojo toda cuenta con algo caucionado — cientos de
descalces falsos el primer día. La celda muestra un `*` y el detalle en el
tooltip cuando hubo garantía descontada, porque es lo primero que se pregunta
cuando aparece una diferencia.

**④ Lo que no se puede comparar no es una diferencia.** Las filas cuyo código de
la Caja no tiene instrumento en `assets` viajan con `vn_aunesa = null` y se
muestran como *"sin comparar"*, con su propio contador. Contarlas como descalce
sería inventar un problema donde lo que falta es una traducción.

### ⏱ EL DESFASAJE HORARIO — por qué hay dos fuentes

**BYMA actualiza sus tenencias después de las 21.** Durante el día, la foto de la
Caja refleja **el cierre anterior**: un bono comprado el viernes en T+1 liquida
hoy, Hygirus ya lo muestra y CVSA todavía no. Comparar contra T0 a las 15 marca
ese desfasaje como si fueran descalces.

Por eso la vista deja elegir contra qué compara:

| | Sale de | Cuándo sirve |
|---|---|---|
| **T0** *(default)* | `tenencia_live` horizonte `t0` | La **conciliación nocturna**: después de las 21 las dos fotos son del mismo momento |
| **CIERRE** | `portafolio.tenencia` | **Durante el día**: la foto conciliada es lo comparable con una CVSA que todavía no actualizó |

Las dos tienen `gar_cantidad`, así que la regla de descontar garantías es la
misma en ambas — no hay caso especial.

Y la pantalla avisa sola: con T0 puesto, si la foto de BYMA es de hoy y todavía
no son las 21, sale un cartel diciendo que use CIERRE. Ese dato vivía en la
cabeza de la mesa; puesto ahí, el que abre la vista a las 15 entiende por qué
hay diferencias en vez de salir a buscar un problema que no existe.

⚠️ `portafolio.tenencia.gar_cantidad` la crea `jobs/portafolio_backfill.py` con
un `ALTER`, y el `CREATE TABLE` de `sql/schema.sql` **no la declaraba**: una base
restaurada nacía sin ella y todo lo que la lee devolvía mal en silencio. Está
agregada como `ALTER ... IF NOT EXISTS`, igual que las ocho columnas de
`clientes.comitentes`.

### Dónde vive el cruce

**En la LECTURA, y no se guarda.** La diferencia es un derivado de dos tablas
vivas; persistirla crearía una tercera copia capaz de quedar vieja mientras las
otras dos se mueven — el patrón que describe `core/duplicados.py`. Calculada en
la query no puede mentir: siempre refleja las dos fotos del momento.

Por eso mismo la cabecera muestra **las dos fechas**. Si no coinciden, la
comparación mezcla dos momentos y cualquier diferencia puede ser eso y no un
descalce: la vista lo canta con un cartel en vez de dejar que se lea como real.

---

## 8. Changelog

- **v1** — Discovery completo contra producción, cliente, maestro de especies
  cargado en `assets.codigo_cnv` (154 completados + 254 que ya coincidían), tabla
  `portafolio.custodia_cvsa`, job diario y vista CUSTODIA en `/back-office`.
  Custody Fees queda sin integrar a la espera de `accountgroupcode`.

- **v2 — MOVIMIENTOS (MVP).** `/transactions/today` como feed, `/transactions`
  como reparador y `POST /transactionsbyreference` como detalle, los tres en
  `core/byma_custodia.py`. Tabla `portafolio.custodia_movimientos` (UPSERT: son
  hechos, no una foto), ingest `POST /api/ingest/custodia/movimientos`, lectura
  `GET /api/back-office/custodia/movimientos` con el plegado de partida doble, y
  tab MOVIMIENTOS en la vista Custodia. Correcciones al portal: `today` es
  asíncrono, el POST **lee**, `currency` es un código. PK provisoria a la espera
  de la medición del primer lote real; el detalle por referencia queda apagado
  hasta el whitelist de IP del Droplet.

- **v3 — LAS CUENTAS, MODELADAS.** `core/custodia_cuentas.py` declara los tres
  espacios de numeración de CVSA y las cinco cuentas con nombre propio.
  `participante` pasa a ser columna y parte de la PK de `custodia_cvsa` y
  `custodia_movimientos` (migración guardada en `schema.sql`, backfilleada desde
  `account_number`, sin re-bajar nada). Los joins con comitentes quedan
  filtrados al espacio `74`. El feed pide los tres espacios en un solo envío. En
  la pantalla, cada cuenta se muestra con su par completo y su etiqueta
  (CLIENTE / LIQUIDADORA / GARANTÍAS), y TENENCIAS suma chips para aislarlas.

- **v3.1** — `74/3` y `74/111111111` declaradas: son cuotapartes de **FCI
  Bilaterales**, no comitentes, aunque vivan en el espacio `74`. Ser comitente
  pasa a decidirse por DOS reglas (espacio + no estar declarada), no solo por el
  prefijo. Los espacios que aparezcan y no estén declarados quedan como
  DESCONOCIDO y **fail-closed**: no se cruzan con clientes. Es el caso de
  `6406` (cheques/pagarés del MAV), que hoy solo aparece en movimientos y se
  declarará el día que haya que mover títulos ahí.
