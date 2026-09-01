# Interbanking — integración

Estado: **V1 COMPLETA EN CÓDIGO** — cliente, esquema SQL, job de sincronización,
endpoints, la tab del back office (dos sub-tabs) y los tests de seguridad. Lo que
queda es de PUESTA EN MARCHA en el Droplet, no de desarrollo: aplicar el schema,
correr el job la primera vez y cargar el cron (ver "Puesta en marcha").

## V1 en una línea

`jobs/interbanking_sync` trae los **extractos** de todas las cuentas cada 2hs
(9-19 ART) a `bancos.*` —el **día hábil anterior y hoy** en cada corrida—, y la
tab **BACK OFFICE → INTERBANKING** los lee de Postgres filtrando por cuenta y
fecha. Solo lectura de punta a punta. El objetivo es **conciliar**.

| Pieza | Archivo |
|---|---|
| Cliente HTTP | `core/interbanking.py` |
| Esquema | `sql/schema.sql` → `CREATE SCHEMA bancos` |
| Ingesta | `jobs/interbanking_sync.py` |
| Lectura | `api/services/bancos.py` |
| HTTP | `api/routers/interbanking.py` (`/api/back-office/interbanking/*`) |
| Cron | `deploy/crontab.txt` → `0 12,14,16,18,20,22 * * 1-5` |
| Seguridad congelada | `tests/unit/test_interbanking_seguridad.py` |
| Ventana hábil congelada | `tests/unit/test_interbanking_ventana.py` |
| Fuente del saldo congelada | `tests/unit/test_interbanking_consolidado.py` |

**Verificado el 2026-08-14** (corrido contra prod, no inferido):

- La autenticación funciona con `POST /cas/oidc/oidcAccessToken`, credenciales
  por **Basic header** y `scope=info-financiera`. Son los defaults de `config.py`,
  así que no hace falta setear los overrides en el `.env`.
- El universo de cuentas son **4 llamadas**, no 2: `account-type` **y**
  `currency` filtran del lado de Interbanking, y `currency` **defaultea a ARS**
  sin avisar (HTTP 200 y menos filas). `todas_las_cuentas()` hace CC/CA × ARS/USD.
- Falta el cruce contra `operaciones.tesoreria_cuentas` (pregunta abierta 1).

## Qué es y para qué sirve

Interbanking es la plataforma por la que ACA opera con sus bancos. Expone 5 APIs
REST de **solo lectura** (todas GET — no hay ningún endpoint que origine pagos,
así que la integración no puede mover plata ni por error).

| API | Endpoint | Qué trae |
|---|---|---|
| Cuentas ✅ | `/accounts`, `/accounts/{n}` | CBU, número, banco (código BCRA + nombre), tipo CC/CA, moneda, denominación |
| Saldos ✅ | `/accounts/{n}/balances` | Contable, operativo actual, **operativo inicial**, proyectados 24/48hs + histórico diario |
| Extractos ✅ | `/accounts/{n}/statements` | Extracto oficial por día: apertura, cierre, totales de débitos/créditos + detalle |
| Movimientos | `/{v1\|v2}/accounts/{n}/movements/{tipo}` | Movimientos del día / anteriores / **diferidos** (fecha futura) / zughus |
| Transferencias | `/transfers/details`, `/transfers/vouchers` | Transferencias en todos sus estados + comprobantes con bloque AFIP (`vep_number`) |

## ⚠️ ESTO NO SE MEZCLA CON TESORERÍA

**Corregido el 2026-08-18, por el user.** Las versiones anteriores de este doc
justificaban la integración diciendo que "tapa tres agujeros de la vista
Tesorería" (el saldo inicial que se tipea a mano, el descubrimiento de cuentas,
la conciliación de la grilla BANCOS). **Eso estaba MAL y nunca se midió** — de
hecho el propio doc listaba "¿son las mismas cuentas?" como pregunta ABIERTA y a
la vez usaba la respuesta afirmativa como fundamento.

Son **dos objetos distintos y sin clave en común**:

| | Tesorería (BANCOS) | Interbanking |
|---|---|---|
| Qué es una "cuenta" | `cuenta_operativa`: **denominación de texto** que manda Aunesa (`'BANCO MARIVA TERCEROS'`, `aunesa_id='57461ARS'`). Es una imputación interna del agente | La **cuenta bancaria** real: banco BCRA + número + CBU + CUIT |
| PK | `(cuenta_operativa, unidad)` | `(bank_number, account_number, account_type, currency)` |
| Fuente | Aunesa `consultaMovDocsSolicitados` + carga manual | el extracto y el saldo que informa el banco |

**No se joinean, no se suman y no se comparan.** Interbanking es la vista del
BANCO, y vive sola. Si aparece una propuesta de cruzar las dos, primero hay que
MEDIR que exista una correspondencia — hoy no está medida.

## Para qué sirve

Ver, **desde el último día hábil**, qué informan los bancos de ACA: el saldo de
cada cuenta y el detalle de lo que se movió. Sin histórico (pedido explícito del
user 2026-08-18): con que figure desde el último día hábil alcanza.

## ⚠️ Dos trampas, ninguna inferible del YAML

**1. El `tokenUrl` que declaran los cinco YAML del proveedor está MAL.**

| | |
|---|---|
| Dicen los YAML | `https://auth.interbanking.com.ar/cas/oidc/accessToken` |
| Declara el servidor | `https://auth.interbanking.com.ar/cas/oidc/oidcAccessToken` |

Todos los demás endpoints del servidor llevan el mismo prefijo (`oidcAuthorize`,
`oidcProfile`, `oidcLogout`), así que el del YAML es un error de documentación.

**Cómo se manifiesta**: 401 con cuerpo genérico de Spring
(`{"error":"Unauthorized","message":"No message available"}`), **idéntico** se
manden las credenciales por Basic, por body o por query string, y también sin
mandar ninguna. Un path inexistente detrás de Spring Security devuelve 401 y no
404, así que el error aparenta ser de credenciales sin serlo.

**Cómo se diagnostica** — y esto vale para cualquier proveedor OAuth, no solo
este: el documento de descubrimiento es público, no lleva credenciales, y es la
fuente de verdad por encima de la documentación que te pasaron.

```
https://auth.interbanking.com.ar/cas/oidc/.well-known/openid-configuration
```

Ahí se confirmó además que `client_credentials` está entre los grants, que
`info-financiera` es un scope válido, y que el servidor acepta tanto
`client_secret_basic` como `client_secret_post`.

**2. Movimientos usa otro base URL.** El resto de las APIs cuelga de
`.../api/prod/v1`; Movimientos de `.../api/prod` con `/v1` o `/v2` en el path.

## Autenticación

Dos cosas **a la vez**, no una — mandar solo una da 401:

- header `client_id: <client_id>` → apiKey del gateway (IBM API Connect)
- header `Authorization: Bearer <token>` → OAuth del CAS

El token se pide con `grant_type=client_credentials` y `scope=info-financiera`.

## Credenciales (.env del Droplet)

```
INTERBANKING_CLIENT_ID=...
INTERBANKING_CLIENT_SECRET=...
INTERBANKING_CUSTOMER_ID=...
```

`CUSTOMER_ID` es el **código de abonado de la empresa** (formato
`^[A-Z][0-9]{5}[A-Z]$`), que sale de Interbanking → Administración → ABM →
Configuración Datos → Datos de Empresa. **Es un dato distinto del `client_id`**:
identifica a la EMPRESA, no a la aplicación. Si ACA tuviera más de una empresa
dada de alta, hay un código por empresa y cada uno ve solo sus cuentas.

Overrides de diagnóstico, para activar desde el `.env` la combinación que
funcione sin tocar código: `INTERBANKING_TOKEN_URL`, `INTERBANKING_AUTH_STYLE`
(`basic` | `post`), `INTERBANKING_SCOPE`.

## Cómo probar

```bash
python -m scripts.diag_interbanking        # DESPUÉS: los datos reales
```

Se pueden correr **desde cualquier PC**, no hace falta el Droplet: las APIs de
Interbanking son internet público y los diags de auth y de forma no tocan la base.
Lo único que necesita DB es el cruce contra `tesoreria_cuentas` del segundo diag,
que está escrito para saltearse solo si no hay conexión.

Un diag (ya cumplido y borrado) probó en matriz endpoint × forma de mandar las
credenciales × con/sin scope, y de cada intento muestra el status, el header
`WWW-Authenticate` (que suele traer el motivo real cuando el cuerpo viene vacío)
y el cuerpo. Si ninguna funciona, sondea el gateway para distinguir
"credenciales mal" de "aplicación no habilitada" — son reclamos distintos al
proveedor.

`diag_interbanking` hace el smoke de las 5 APIs y cruza el listado de cuentas
contra `operaciones.tesoreria_cuentas`.


## Límites

- **100 llamadas por minuto** (plan contratado). `core/interbanking.py` throttlea
  a 80: el límite es del ABONADO, no del proceso, así que dos procesos del
  Droplet consumen del mismo pozo.
- Consultas históricas: **180 días hacia atrás, en ventanas de 60 días** por llamada.
- `account-type` **y `currency` filtran**, y `currency` defaultea a **ARS** sin
  avisar: el universo completo son **cuatro** llamadas (CC/CA × ARS/USD). Eso hace
  `todas_las_cuentas()`.
- El tope de 60 días por consulta es de **calendario**: la ventana del job cuenta
  días hábiles pero se recorta a 60 corridos antes de salir a la API.

## Preguntas abiertas (a medir con los diags, REGLA #2)

1. ¿Las cuentas de Interbanking son las mismas que hoy están en `tesoreria_cuentas`?
2. ¿`initial_operating_balance` coincide con el saldo inicial que carga el back office?
3. ¿El extracto cierra? (`apertura + créditos − débitos == cierre`)
4. ¿Los movimientos traen un **ID estable**? v1 declara un campo `id` que v2 no
   tiene. Si es estable entre llamadas, resuelve la idempotencia al persistir —
   que es justo el problema que hoy obliga a que los movimientos de Aunesa sin
   hora arranquen destildados en el modal de auditoría.
5. ¿Qué valores toma de verdad el `status` de las transferencias? El YAML no los enumera.
6. ¿Los comprobantes traen el `vep_number` que hoy se tipea a mano en la tab VEPS?

Hasta tener esos números medidos **no se decide el modelo de datos**.

## Decisiones de diseño de la V1

**Extractos + Saldos. Movimientos NO.** Extractos devuelve el día (apertura,
cierre, totales) *y* su detalle de movimientos en la misma respuesta. Traer
además la API de **Movimientos sería la misma data dos veces** — medido: los dos
endpoints devolvieron `total_rows` idéntico para el mismo rango y cuenta. Lo
único que Movimientos tiene y Extractos no son los **diferidos** (fecha futura),
que siguen sin traerse.

**Saldos se sumó el 2026-08-18** y NO es una segunda verdad: contesta otra
pregunta. Extractos dice *qué pasó* y **solo existe si hubo movimientos**;
Saldos dice *cuánto hay*, se haya movido la cuenta o no. Con la ventana corta que
usa el back office (último día hábil + hoy), una cuenta quieta no tenía NINGUNA
fila y el consolidado la mostraba con «—» — cuanto más corta la ventana, más
grande el agujero. Van en tablas separadas (`extracto_dia` / `saldos`), por
cuenta gana una sola y la respuesta declara cuál en `fuente`; si el banco informa
las dos y no coinciden, eso se publica como `discrepancia` en vez de elegir una
y tapar la otra. **Transferencias sigue sin usarse.**

**La vista NUNCA le pega a Interbanking.** El límite de 100 llamadas/minuto es
del **ABONADO**, no del proceso: unos pocos usuarios refrescando la pantalla
podrían agotar la cuota y romper el propio job, y cualquier otro sistema de ACA
que use esa cuota. El job escribe, la vista lee de Postgres. De yapa, la pantalla
es instantánea y sigue funcionando si Interbanking está caído.

**El día HÁBIL anterior y hoy en cada corrida.** Un movimiento puede aparecer o
corregirse después del cierre del banco. Re-pedirlo cuesta una llamada por cuenta
y la ingesta es idempotente, así que correrla de más no duplica nada.

⚠️ **Hábil, no calendario** (corregido el 2026-08-18 — ver changelog). Los bancos
no operan sábados, domingos ni feriados: un día no hábil no tiene extracto. Restar
un día corrido apuntaba a un día vacío y dejaba el último día CON actividad sin
re-sincronizar. Pasaba **todos los lunes** (la ventana caía en domingo y el viernes
no se volvía a pedir nunca) y los martes post-feriado. La ventana la calcula
`jobs/interbanking_sync.ventana()` con `core.calendario.restar_habiles`, y el rango
por defecto de la vista sale de `bancos.rango_default()` con **la misma primitiva**:
si divergieran, la pantalla pediría un día que el job nunca trajo. Congelado en
`tests/unit/test_interbanking_ventana.py`.

El rango SÍ incluye los días no hábiles del medio (el finde entre viernes y lunes):
viajan en la misma llamada, no cuestan nada extra y vienen vacíos. El tope de 60
días de la API es de **calendario**, así que `--dias` cuenta hábiles pero el rango
se recorta a 60 corridos.

**El hash como PK de los movimientos.** No hay id natural (ver arriba). El hash
incluye importe, tipo y código además de (extracto, correlativo): si el banco
corrige un movimiento, preferimos una fila NUEVA antes que pisar la vieja en
silencio. **Duplicar es visible; perder no.** Y se detecta solo: la ingesta
compara lo que guardó contra el `total_movimientos` que declara el extracto de
ese día y lo reporta en `bancos.sync_log.incoherentes`.

**`cierra` y `diferencia` se materializan en la ingesta.** `apertura + créditos −
débitos == cierre` es la primera pregunta de cualquier conciliación; no puede
depender de que alguien la calcule bien en la vista.

**`raw jsonb` en cada tabla.** Mismo patrón que `mercado.curvas.data`. La API
devuelve más de lo que documenta (`account_cuit`, `associated_voucher`,
`grouping_code_standard`, un `addenda` con retenciones): si mañana hace falta un
campo, está en la base y no hay que re-pedir el histórico.

## Seguridad

| | |
|---|---|
| **Quién ve** | módulo `back-office` (`_BACK_OFFICE` en `api/main.py`) |
| **Portal invitado** | **JAMÁS** (REGLA #8), congelado por test |
| **Escritura hacia Interbanking** | imposible: el cliente solo implementa GET, congelado por test |
| **Escritura desde la vista** | ninguna: el router solo expone GET, congelado por test |
| **CBU / CUIT de nuestras cuentas** | se guardan, **nunca** se serializan al front |
| **Número de cuenta** | al front va solo la terminación (`…0020`) |
| **CUIT de contraparte** | enmascarado (`20-…-9`) — son datos de terceros |
| **`raw` jsonb** | nunca sale de la base |
| **Credenciales** | `.env` del Droplet; el front nunca ve un token de Interbanking |
| **Auditoría de lectura** | `bancos.audit_lecturas` — quién miró qué cuenta y cuándo |
| **IA** | nada de esto va al copiloto ni al asistente. Si algún día se quiere, pasa por `core/pii_gateway.py` |

## Puesta en marcha (Droplet)

```bash
cd /root/TradingAV && git pull
python -m scripts.apply_schema              # crea el esquema bancos
python -m jobs.interbanking_sync --solo-cuentas   # 4 llamadas: siembra el maestro
python -m jobs.interbanking_sync            # ayer + hoy, todas las cuentas
```

Después el cron lo mantiene solo (la línea vive en `deploy/crontab.txt`; si el
Droplet todavía no la tiene cargada, el job no corre aunque el código esté).
`--dry` no escribe nada y `--dias N` cuenta días **hábiles**.

## GASTOS BANCARIOS — el modelo

Separar, dentro de los movimientos del día, lo que **el banco se cobró**
(comisiones, mantenimiento, sellados). Ya está implícito en el saldo: **esto no
cambia ningún número, lo DISTINGUE.**

Son **dos capas**, y la diferencia entre ellas es la idea central:

| | Qué es | Dónde vive | Alcance |
|---|---|---|---|
| **Regla** | El **conocimiento**. «Todo lo que tenga el código 830 es gasto» | `bancos.gastos_reglas` | Todos los bancos, todos los días, para siempre |
| **Override** | El **parche**. «ESTE movimiento, hoy, es (o no es) gasto» | `bancos.gastos_overrides` | Un movimiento |

**El override GANA siempre sobre la regla.** Si una persona lo decidió, la
máquina no se lo da vuelta — mismo criterio que `tesoreria_exclusiones` y que
`assets.vigencia_motivo='manual'`.

**Si el equipo se encuentra marcando lo MISMO todos los días, eso no es un
override: es una regla que falta.** Por eso la vista muestra de dónde salió cada
marca (subrayado = manual) — para que ese patrón se vea.

Una regla es `campo` + `operador` + `valor`:
- **campo**: `codigo_ib` · `codigo_banco` · `descripcion_banco` · `descripcion_ib`
- **operador**: `igual` (el código exacto) · `contiene` (la palabra en la
  descripción — con esto se arranca cuando todavía no se sabe qué códigos usa
  cada banco)

⚠️ **`descripcion_banco` significa lo que la pantalla MUESTRA bajo DESCRIPCIÓN**,
que no es la columna cruda: es `descripcion_banco` y, **cuando el banco no manda
la suya, el CONCEPTO** (lo decide `_movimiento_publico`, y existe porque una fila
sin ningún texto es ilegible). Las dos mitades del criterio —¿es gasto? y ¿en qué
balde cae?— leen por **`bancos.valor_campo()`**, la única puerta, para que un
campo no pueda significar una cosa en las reglas y otra en el desglose.

**No hay fallback al revés**: la columna CONCEPTO muestra `descripcion_ib` crudo y
un «—» cuando está vacío, así que una regla de CONCEPTO tampoco mira otra cosa.
Espejar la pantalla es la regla; inventar un derivado que nadie ve sería el mismo
error otra vez (ver el changelog del 2026-09-01).

⚠️ El `campo` lo escribe un usuario y **nunca viaja a un `WHERE`**: se traduce
contra `CAMPOS_REGLA`, un dict del módulo. Un campo que no esté ahí no matchea.
La clasificación es una función **pura** (`clasificar`) con tests propios: decide
un número que se lee como plata y falla en silencio — si se rompe, no hay
excepción, solo otro total.

**No se materializa**: se resuelve en la LECTURA. Cambiar una regla se refleja al
instante en los 3 días que hay en la base, sin recomputar, y el total de la
grilla no puede contradecir al catálogo.

**Signo**: un débito SUMA (el banco cobró), un crédito RESTA (lo reintegró). Es
el gasto NETO del día, no la suma de valores absolutos — que contaría dos veces
un cobro mal hecho y su devolución.

**Sin una sola regla ni marca, la columna muestra «—» y no 0**: nadie afirmó que
el banco no cobró nada.

**Permiso de escritura**: la allowlist de Tesorería (`tesoreria_escritores`) +
admin. Se reusa a propósito — una lista nueva nace vacía y la función quedaría
muerta hasta que alguien la cargue, siendo el mismo equipo en la misma pantalla.
Compartir una allowlist **no acopla los datos**: un permiso es una política sobre
personas, no un join. Todo queda en `bancos.gastos_audit`.

## El DESGLOSE de los gastos

El total no alcanza: el back office necesita ver **cuánto de ese total es qué**.
Es una separación de **presentación** — no cambia ningún número, parte el que ya
está. Cada gasto cae en **exactamente un** balde (gana el primero que matchea).

| Balde | Mira | Cómo |
|---|---|---|
| IVA · IVAPERCEP · IIBBPERCEP | `descripcion_ib` (CONCEPTO) | **`igual`** |
| **COM.TRANSF** | `descripcion_ib` **Y** `descripcion_banco` | `contiene` |
| IMP.DB/CR P/CRE · IMP.DB/CR P/DEB · SELLOS · TASA LIQUIDEZ | `descripcion_banco` | `contiene` |

⚠️ **El CAMPO va en el MATCHER, no en el balde.** Lo obligó COM.TRANSF: es el
mismo cobro y llega de **tres formas** según el banco — como CONCEPTO abreviado
(`COM.TRANSF`) o escrito en la DESCRIPCIÓN (`COMISIONES DATANET`,
`COMISION ECHEQ CLEA`). Las tres suman a la misma columna. Con el campo a nivel
del balde eso no se podía expresar.

⚠️ **El orden salva el caso ambiguo**: un movimiento con concepto `IVA` y
descripción `COMISIONES DATANET` es el **IVA de esa comisión**, no la comisión.
Como los conceptos van primero, cae en IVA. Si cayera en COM.TRANSF, esa columna
mostraría de más y IVA de menos **con el total dando bien** — un error invisible.
Congelado por test.

⚠️ Los tres primeros van por **`igual`** y no por `contiene`: **«IVA» es prefijo
de «IVAPERCEP»**, así que con `contiene` la columna IVA mostraría de más y
IVAPERCEP quedaría en cero. Cuando un valor es prefijo de otro, `contiene` no
sirve. Congelado por test.

**`IMP.DB/CR P/DEB` tiene DOS grafías** (`IMP.DB/CR BANCARIOS P/DEB` en Patagonia,
`LEY25413DB` en BIND): es el mismo impuesto, un balde, dos matchers.

**Dónde se ve cada cosa:**
- **CONSOLIDADO** — una columna por CONCEPTO + **OTROS IMP**, que es la suma de
  las **4 descripciones** (y solo esas: decisión del back office).
- **MODAL** — el desglose completo en horizontal: ahí los cuatro impuestos se
  abren de a uno.

⚠️ **Las columnas pueden NO sumar el total**, justamente porque OTROS IMP son solo
esas cuatro. El gasto que no cae en ningún balde **no se reparte a dedo**: va a
`resto` y el modal lo muestra como **MOVIMIENTOS RESTANTES** cuando no es cero —
mismo criterio que `sin_clasificar` en la vista ACA. Esconderlo adentro de otra
celda sería inventar dónde va. **Esa celda es además el tablero de lo que falta
cargar**: si crece, hay una grafía nueva que ningún balde agarra.

### El catálogo lo edita el EQUIPO, no el código

Era una **constante** en Python, con el argumento de que "qué columnas tiene una
tabla no se cambia todos los días". **Duró un día**: el back office encontró un
impuesto que ningún balde agarraba y la única forma de sumarlo era que alguien
tocara código y deployara. Eso es exactamente lo que no puede pasar — **el que
sabe que el Banco X escribe `LEY25413DB` donde el Y dice `IMP.DB/CR BANCARIOS
P/DEB` es el equipo**, no el que programa.

Desde el 2026-08-18 el catálogo vive en la base y se edita desde el botón
**DESGLOSE** del modal de movimientos:

- `bancos.gastos_baldes` — la columna: `etiqueta`, `grupo` (`concepto` = columna
  propia en el consolidado · `otros` = se suma adentro de OTROS IMP) y `orden`.
- `bancos.gastos_balde_matchers` — las **grafías**: `campo` + `operador` +
  `valor`. Agregar una es el 90% del uso.

Tres cosas que el diseño sostiene:

1. **La semilla es el estado inicial, no la verdad.** `DESGLOSE_SEMILLA` (Python)
   se carga **una sola vez**, cuando la tabla está vacía. Cambiarla después no
   toca una base ya sembrada, y un balde borrado a propósito **no vuelve solo**.
   `semilla_catalogo()` la devuelve con la MISMA forma que `_baldes()` lee de la
   base — así lo que prueban los tests es exactamente lo que se siembra.
2. **`orden` no es cosmético: es lo único que decide los empates.** Gana el
   primer balde que matchea. Por eso una columna nueva nace **al final** — colarla
   antes cambiaría dónde caen movimientos que hoy ya están bien clasificados. Se
   mueve con ▲▼ desde la pantalla, y ese movimiento es la herramienta para
   resolver un pisón (ver abajo).
3. **Cuesta UNA query por request** (baldes + matchers en un `LEFT JOIN`), y los
   topes del test de queries subieron de 9/8 a **10/9** a propósito. Es el precio
   de que el equipo no dependa de un deploy, y está medido: ~8,5ms.

El desglose se **deriva en la lectura**, así que un cambio se ve en el próximo
poll, sin recomputar nada y sin poder contradecir al catálogo. Y borrar un balde
no rompe nada: sus movimientos pasan a MOVIMIENTOS RESTANTES y **ningún total
cambia**.

### Borrar: solo una columna VACÍA

**Incidente 2026-08-19.** Alguien borró COM.TRANSF **con sus tres textos** de un
clic, y **no se pudieron recuperar**: la baja auditaba la clave y el nombre de la
columna, y los matchers se iban por `ON DELETE CASCADE` **sin quedar registrados
en ningún lado**. Dos errores en uno — un botón demasiado fácil de apretar y una
auditoría que guardaba la mitad — y el que los cometió fui yo al escribirlo.

Las dos cosas quedaron arregladas:

- **No se puede borrar una columna con textos cargados** (lo exige el backend, y
  la pantalla directamente no muestra el ✕). Primero hay que sacarlos de a uno, y
  **cada uno queda auditado por separado** con su campo, operador y valor. Así el
  gesto destructivo se vuelve deliberado en vez de instantáneo, y siempre queda
  de dónde reconstruir. Una columna **vacía** sí se borra: no hay conocimiento
  que perder, y es el caso real de «me equivoqué al crearla».
- **La auditoría guarda el balde completo, con sus textos.** Aunque hoy no se
  pueda borrar uno cargado, si mañana alguien afloja la regla el rastro ya está.
  Cuesta una línea; que el dato no se evapore no tiene precio.
- Sacar un texto pide **confirmación** y dice qué se lleva puesto.

> El patrón general, que vale para cualquier ABM de este repo: **una baja tiene
> que auditar lo que se lleva, no lo que se ve.** Auditar el padre y dejar que
> los hijos se vayan por cascada es exactamente la forma de perder datos sin
> enterarse.

⚠️ **Las dos trampas están explicadas DENTRO de la pantalla**, no solo acá:
`contiene` vs `es igual` (con `contiene`, «IVA» se come «IVAPERCEP» — el total
sigue dando bien y dos columnas quedan mal) y qué significa el orden. Un ABM que
deja meter la pata en silencio es peor que no tenerlo.

### Cuando dos columnas se pisan: se mueve el ORDEN, no se agrega una excepción

El caso real (2026-08-19): un banco manda **`IVA PERCEPCION RESOL GRAL` con el
CONCEPTO en `IVA`**. Como IVA se evaluaba antes, se lo comía: IVA mostraba de más
e IVAPERCEP quedaba en cero, **con el total dando bien**. El error invisible de
siempre.

Había dos salidas y la diferencia entre ellas es la que importa:

- **Una excepción para ese texto en el código** — una regla escondida, que solo
  puede cambiar quien programa y que nadie más sabe que existe. Y el próximo caso
  va a ser parecido pero distinto, así que serían dos excepciones. Y después tres.
- **Subir IVAPERCEP arriba de IVA** y darle un matcher por **descripción**
  (`descripcion_banco contiene IVA PERCEPCION RESOL`). Un movimiento con esa
  descripción cae en IVAPERCEP; **uno con concepto IVA y sin esa descripción
  sigue cayendo en IVA**, porque el matcher no lo agarra.

La segunda no es un parche: **es el mecanismo funcionando**. Por eso lo que se
agregó es *poder mover el orden desde la pantalla* (▲▼), no una excepción — el
mismo movimiento resuelve el próximo caso. Hay tests que congelan las dos mitades:
que el pisón se arregla, y que el IVA común **no** se rompe al arreglarlo.

`POST /gastos/desglose/orden` recibe la lista **completa** de claves en el orden
nuevo y reasigna la secuencia entera (10, 20, 30…). Media lista dejaría unas
columnas con el orden viejo y otras con el nuevo — empates silenciosos.

## El AUDITOR del desglose

**Clic en cualquier número del desglose → la tabla queda mostrando SOLO las filas
que lo componen.** Mismo gesto que el modal por celda de Tesorería → BANCOS: un
total que no se puede abrir es un total en el que hay que creer.

Filtra la tabla de abajo en vez de abrir otro modal encima — las filas ya están,
con todas sus columnas; apilar modales para mirar lo mismo es ceremonia.

La barra del filtro muestra **cuántas filas** y **cuánto suman**, y esa suma la
calcula la PANTALLA sobre las filas visibles, no la copia del backend. Es a
propósito: si no coincide con el número que clickeaste, el desglose y el detalle
se contradicen, y eso es exactamente lo que un auditor tiene que dejar ver.

Cada movimiento viaja con su `gasto_balde` (lo asigna `vista()` con la MISMA
función que arma el desglose), así que el filtro no puede seleccionar un conjunto
distinto del que sumó.

**El descargar respeta el filtro**: se baja lo que se está viendo. Bajar la lista
completa con un filtro puesto sería darle al usuario algo distinto de lo que pidió.

⚠️ Con el auditor, el clic en el VALOR pasó a ser "ver las filas" y el **copiar se
mudó a un ícono ⧉ al lado de la etiqueta** — chico pero SIEMPRE visible, no en un
hover: cuando el gesto principal cambia, lo que se desplaza necesita su propio
lugar o desaparece.

## IGNORAR un movimiento

**Dos preguntas distintas, dos columnas.** La columna **GASTO** dice *qué es* el
movimiento (lo cobró el banco o no). La columna **CUENTA** dice *si suma*. Un
duplicado del banco sigue siendo un gasto — lo que no es, es **dos** gastos, y
con una sola columna no había forma de decir eso.

Es el mismo modelo que el destildado por celda de Tesorería → BANCOS, y las
mismas tres propiedades:

- **La fila NO se borra.** Queda **tachada y apagada**, con su observación
  (quién y cuándo, y el motivo si lo escribieron). Esconderla haría que el
  detalle deje de coincidir con el extracto del banco — que es justamente contra
  lo que se concilia. Y esconder una fila esconde la decisión que alguien tomó
  sobre ella.
- **Tabla de PUROS OVERRIDES** (`bancos.movimientos_ignorados`): la ausencia de
  fila significa "cuenta". Des-ignorar es un `DELETE`, no un flag en `false` —
  así no existe el estado ambiguo de una fila que dice `ignorado = false` y
  compite con el default.
- **`ON DELETE CASCADE`**: cuando la retención de 3 fechas borra el movimiento,
  su marca se va con él. Una marca sin movimiento no significa nada.

**Qué deja de sumar:** los GASTOS y su desglose, tanto en el modal como en la
columna del consolidado. Lo mismo vale para el **auditor**: la suma de las filas
visibles excluye los ignorados y los cuenta aparte («N ignorado(s), fuera de la
suma»), porque si sumaran, ese número nunca coincidiría con el del desglose y el
auditor acusaría una diferencia que no existe.

**Qué NO toca:** los créditos/débitos del día. Esa es la aritmética del extracto
del banco, y restarle una fila haría que la vista contradiga al extracto.

**Ignorar gana sobre la marca manual.** Marcar «esto ES gasto» y después
ignorarlo da cero — son dos preguntas y la segunda es la que decide si entra al
total. Hay un test que lo congela.

Escritura: `PUT /gastos/ignorar`, detrás de la misma allowlist que el resto
(Tesorería + admin) y auditado en `bancos.gastos_audit`. Vive bajo `/gastos/`
porque lo que deja de sumar son los gastos — de paso, el proxy de Next no
necesitó ampliar su superficie de escritura.

## REPORTE FIN DE DÍA

El saldo al cierre de **todas** las cuentas, para pasar hacia afuera.

⚠️ **Una tabla POR BANCO, no una matriz.** Se probaron las dos matrices —bancos en
las columnas y después bancos en las filas— y **las dos fallan por lo mismo**:
cada cuenta pertenece a UN banco, así que en una grilla común la enorme mayoría
de las celdas queda vacía. El resultado era una tabla larguísima o anchísima al
pedo, con el dato disperso en un mar de blanco. Es el caso de manual de cuándo
NO usar una matriz: **los datos no son un producto cartesiano**, son una relación
de uno a muchos, y una tabla por banco es su forma natural.

Con una tabla por banco, cada una mide lo que su banco necesita —dos filas si
tiene dos cuentas, seis si tiene seis— y las tablas se **acomodan** una al lado de
la otra hasta llenar el espacio. Cero celdas vacías.

- **El acomodado es un empaquetado explícito, no `columns` de CSS**: así una
  tabla **nunca se parte al medio**, que es justo lo que hace el flujo de CSS y
  lo que volvería ilegible el reporte.
- **Bin packing con los bancos GRANDES primero.** Es la idea del back office:
  Banco Valores tiene 9 cuentas e Industrial 5, y puestos uno abajo del otro
  llenan una columna entera; después los chicos (Coinag 3, Comafi 4, Galicia 5)
  rellenan los huecos. **Al revés no cierra**: tomándolos en el orden que vienen,
  los chicos ocupan las primeras columnas, el banco de 9 ya no entra en ninguna y
  se abre una columna nueva casi vacía — que es lo que dejaba media pantalla en
  blanco y una tabla afuera de la foto. Cada banco va a la columna que está más
  vacía en ese momento; para 9 elementos alcanza y sobra.
- ⚠️ **El MODAL se ajusta al contenido, no al revés.** Se probaron las tres
  alternativas y las tres fallan: ancho fijo con tablas que miden lo suyo deja
  media pantalla en blanco al costado; ancho fijo con las tablas estiradas a `1fr`
  las deforma; y medir y **escalar** todo para que entrase dejaba la letra
  ilegible. Lo que funciona es lo simple: las columnas se dimensionan por su
  **contenido** (`max-content`) y el modal toma el ancho que eso pide, hasta el
  borde de la pantalla.
- Las columnas van en **grid** (`grid-auto-flow: column`) y no en `flex-wrap`:
  con flex, una columna que no entra por ancho se va a un renglón nuevo y el
  reporte se desarma —una columna larguísima, media pantalla en blanco al lado y
  los bancos que siguen abajo del fold.
- **Los espacios entre tablas son grandes a propósito**: son lo único que dice
  que cada bloque es una tabla independiente y no la continuación de la de al
  lado.
- Adentro de cada banco, **ARS primero** y una línea más marcada donde cambia la
  moneda: leer pesos y dólares en la misma corrida visual es el error que este
  formato evita.

### COPIAR IMAGEN

Botón de la barra: genera el PNG del reporte y lo deja en el portapapeles para
pegarlo en un mail.

⚠️ **No es una captura de pantalla: el reporte se DIBUJA de cero en un canvas**
(`src/lib/reporte-imagen.ts`) a partir de los mismos datos que la tabla. Se
descartaron las dos alternativas: `html2canvas` es una dependencia grande que
reimplementa el motor de layout del navegador y falla justo con lo que esta app
usa (variables CSS, grid, temas); y `SVG + foreignObject` obliga a inlinear todo
el CSS a mano y se rompe en silencio cuando cambia una clase.

Dibujarlo da tres cosas concretas:

- el resultado **no depende de la pantalla** del que lo manda — el mismo mail
  desde una notebook chica o desde un monitor grande;
- sale **siempre en claro** aunque la app esté en oscuro: un mail con fondo negro
  se imprime pésimo;
- **nada de la UI puede colarse** en la imagen, porque el botón, el scroll y el ✕
  no existen para el canvas. No hay que acordarse de esconder nada.

Se dibuja al **doble de resolución** para que no se vea borroso cuando el cliente
de mail lo agranda. Si el navegador no deja copiar imágenes (Firefox, o cualquier
origen sin HTTPS) **la descarga y lo dice**: el objetivo es que la imagen llegue
al mail, y quedarse en un error no la lleva a ningún lado.

El orden de las cuentas lo comparten la tabla y la imagen (`cuentasOrdenadas`):
si cada una ordenara por su cuenta, lo que se pega en el mail podría no coincidir
con lo que se está mirando. Los saldos viajan **ya formateados** por el mismo
motivo.

La barra lleva la firma **«Hecho en ACAQuant»** en chico —en la pantalla y
adentro de la imagen—: si el reporte termina reenviado tres veces, sigue diciendo
de dónde salió.

Cada fila dice **exacto** lo mismo que la columna CUENTA del consolidado
(tipo · moneda · número, con la etiqueta **debajo** del número y no al lado): si
dijera otra cosa, el que compara las dos pantallas tendría que traducir. El día
es el **mismo** que muestra la vista, así el reporte no puede decir algo distinto
de la pantalla desde la que se abrió. Cabecera en el azul de la casa con el logo,
**una sola vez** arriba de todo: se muestra y se captura, no es una pantalla de
trabajo.

## BANCOS Y MOVIMIENTOS MANUALES

Interbanking no tiene todos los bancos de la casa, y el que falta igual mueve
plata. Son dos cosas que van juntas porque son la misma necesidad.

### Las cuentas manuales viven en la MISMA tabla

`bancos.cuentas.origen` = `interbanking` | `manual`. **No** hay una segunda tabla:
son cuentas bancarias, se muestran juntas y se leen igual — separarlas obligaría
a unir dos fuentes en cada lectura y a duplicar cada cambio de acá en adelante.

⚠️ **El job no las puede pisar, y no hizo falta ninguna defensa nueva**: el job
recorre lo que le devuelve Interbanking, y una cuenta manual —por definición— no
está en esa lista. Lo único que se agregó es que el upsert **no toca `origen`**:
si algún día Interbanking empieza a informar una cuenta que se había cargado a
mano, se completa con datos reales pero **sigue marcada como manual**, que es la
información que hace falta para decidir qué hacer con sus movimientos manuales.
Nunca al revés — blanquearla la perdería en silencio.

**El banco se resuelve por NOMBRE.** Si ya existe uno con ese nombre, la cuenta
nueva hereda su `bank_number` y queda agrupada abajo de él; si no existe, se le
genera un código propio que arranca con `M` (los del BCRA son tres dígitos, así
que no pueden chocar). Con un solo formulario se resuelven las dos cosas —
sumarle una cuenta a un banco que ya está, o dar de alta un banco entero— y al
usuario no se le pide un "código de banco" que no tiene.

Borrar una cuenta **solo si es manual**: las de Interbanking las da de alta el
job y borrarlas sería pelearse con él todos los días.

### Los movimientos manuales SIEMPRE impactan el saldo al cierre

`bancos.movimientos_manuales` — mismo modelo que los REGISTROS MANUALES de
Tesorería: una fuente de plata que no viene de ninguna API.

- **El ajuste se aplica venga el saldo de donde venga.** En una cuenta real se
  suma arriba de su extracto (`fuente` sigue diciendo `extracto`); en una cuenta
  manual, donde no hay ni extracto ni saldo del banco, **el saldo ES la suma de
  estos movimientos** y `fuente` vale `manual`. Una cuenta sin nada de nada sigue
  mostrando «—»: «no sabemos» no es «cero».
- Se publica `ajuste_manual` aparte para que la vista lo pueda cantar (`±man` en
  la celda): un saldo ajustado a mano y uno informado por el banco no se leen
  igual.
- `tipo` C/D en vez de un importe con signo, igual que `bancos.movimientos`: así
  un movimiento manual se dibuja en la misma tabla que los del banco, con las
  mismas columnas, y no hay dos convenciones de signo conviviendo.
- **Créditos y débitos del resumen NO los incluyen.** Esa es la aritmética del
  extracto y es contra lo que se concilia; si entraran, la vista dejaría de poder
  compararse con lo que informa el banco. Lo que los manuales mueven —el saldo—
  viaja aparte.
- ⚠️ **La retención de 3 fechas NO los purga.** Los movimientos del banco se
  vuelven a pedir cuando hagan falta; esto lo tipeó una persona y no se puede
  reconstruir. Por eso `purgar()` solo toca las tablas que el job escribe.

En la vista: **el día no se elige** en el formulario (es el que muestra la
pantalla — con su propio selector se podría cargar un ajuste en un día que nadie
está mirando) y **la moneda tampoco** (cada cuenta ya es de una moneda, y
preguntarla sería ofrecer la posibilidad de contradecirla). Elegido el banco, el
selector de cuenta se llena solo con las cuentas de ESE banco, que es lo que evita
cargarle un movimiento a la cuenta de otro banco con número parecido.

## CONCILIAR contra el mayor contable

Compara **un número contra otro**: nuestro saldo al cierre y el **último saldo**
del mayor que el usuario sube como Excel. Si no coinciden, busca qué movimientos
del día explican la diferencia — porque el caso típico es al revés de lo que
parece: no es que el banco tenga de más, es que **al mayor le falta registrar
algo que el banco sí informó**.

⚠️ **El frontend existía desde el 2026-08-19 y el backend NO**: la vista llamaba
a `POST /conciliar`, que no estaba montado, y la pantalla mostraba «Not Found» —
el `detail` con que FastAPI contesta una ruta inexistente. Media feature
mergeada es peor que ninguna: parece que anda hasta que alguien la usa.

### El formato del saldo, MEDIDO (no supuesto)

Sobre el export real (`mayor_36.xlsx`, Credicoop, 2026-08-18), dos cosas que
parecían obvias y no lo eran:

1. **El valor de la celda ya viene FIRMADO** (`-499946423.26`). La `D`/`A` que se
   ve en Excel **es formato de celda, no texto**: el `numFmt` es
   `#,##0.00" D";#,##0.00" A"` — dos secciones, y **la segunda es la NEGATIVA y
   no lleva el menos**. O sea: `A` = acreedor = negativo, con el número al lado
   en valor absoluto.
2. El front lee con `raw: false` justamente para no perder esa letra, así que al
   backend le llega el **texto formateado**, no el número.

Por eso `_num_mayor` acepta **las dos formas** —número crudo o texto— y decide el
separador decimal **por posición** (gana el último `.` o `,`, salvo que le sigan
exactamente 3 dígitos y sea el único): `1,234.56`, `1.234,56` y `500.000` se leen
bien sin preguntarle a nadie de qué país es el archivo. Un saldo mal leído es una
conciliación que miente, y eso no avisa.

### Decisiones

- **La columna SALDO se busca por ENCABEZADO**, no por posición: el export puede
  traer títulos arriba o columnas de más. Si no la encuentra, usa la última con
  números **y lo avisa** — adivinar está bien, adivinar en silencio es cómo un
  número equivocado pasa por bueno.
- Se devuelve la **evidencia** (`saldo_excel_texto`, `saldo_excel_fila`): el que
  concilia tiene que poder verificar de dónde salió el número **sin abrir el
  Excel al lado**.
- **Nuestro saldo es el MISMO que muestra el consolidado**, ajuste manual
  incluido (y avisado). Si acá se usara el saldo pelado del banco, dos pantallas
  dirían dos números para la misma cuenta y el mismo día.
- **Los dos detalles, uno de cada lado — pero NO al 50%.** El concepto del mayor
  es larguísimo (`[Op. 1131651] Pago c/retención ganancias RG 830 - …`) y la
  descripción del banco entra en un renglón: partir la pantalla por la mitad
  dejaba aire de sobra a la izquierda y cortaba justo lo que hay que leer a la
  derecha. La grilla reparte **4fr / 6fr** con `minmax(0, …)` —sin eso una
  columna de grilla nunca baja del ancho de su contenido y la tabla se desborda
  igual—, las filas van apretadas (10px, `leading` corto) y la descripción
  **rompe de renglón** en vez de empujar al importe fuera de la vista.
- **Los dos detalles, uno de cada lado.** Solo descripción e importe: los
  movimientos del banco y los del mayor **no tienen nada en común** en fechas ni
  comprobantes (`[Op. 1130699] bco a bco` contra `TRANSF.O/BANCOS MISMO TIT`),
  así que mostrarlos invitaría a cruzarlos por donde no se puede. Lo único
  comparable es el **importe**, y por eso las dos columnas de números quedan
  alineadas a la misma altura: el ojo hace la comparación solo.
- ⚠️ **`importe del mayor = Debe − Haber`**, medido y verificado. **Una fila es
  un movimiento si tiene FECHA** — nada más. Hubo una versión que además
  identificaba el «saldo inicial» y auto-verificaba el parseo (inicial +
  movimientos = saldo final), y **el chequeo se sacó**: el formato del mayor
  admite hasta 7 decimales (`#,##0.00#####`), así que un `1.515.504,677` es
  genuinamente ambiguo contra un separador de miles — el saldo inicial se leyó
  **mil veces más grande** y el aviso salió gritando en un archivo perfecto.
  **Un aviso que grita cuando no pasa nada entrena a ignorar todos los avisos**,
  incluidos los que sí importan: si el dato que lo alimenta no es confiable, el
  chequeo no es una ayuda extra, es ruido con cara de hallazgo. La fecha, en
  cambio, no es ambigua — y es la definición de movimiento.
- ⚠️ **Hay dos categorías de explicación, y no compiten entre sí.**
  · **Por BÚSQUEDA** (`_buscar`): elige un subconjunto que sume la diferencia.
    Nunca mezcla signos ni lados — elegir un ingreso y un egreso hasta que dé el
    número es una coincidencia aritmética, no una explicación.
  · **Por CONSTRUCCIÓN**: el conjunto ENTERO de lo que no calzó de un lado. **Sí
    puede mezclar signos**, y no contradice lo anterior: acá no se elige nada. Si
    al mayor no le quedó nada sin calzar, que todo lo que le falta sea todo lo
    que al banco le sobró **no es un hallazgo, es una identidad**.

  ⚠️ Nace de un caso real (Patagonia, 19/08/2026): diferencia de `4.256.787,71`
  que eran los **once** movimientos del banco sin calzar — 4 créditos grandes y 7
  débitos que eran exactamente los gastos bancarios del día. `_buscar` no podía
  encontrarlo por **dos motivos a la vez**: signos mezclados (prohibido, y con
  razón) y once movimientos contra un tope de ocho. La pantalla decía «ningún
  movimiento llega a esa diferencia» **con la respuesta entera a la vista**.
  La salida NO fue aflojar `_buscar` —eso habría empezado a inventar
  explicaciones en todos los demás casos— sino ver que **esto no era una
  búsqueda**. Congelado por test.
- Las **explicaciones** son subconjuntos de movimientos que llegan a la
  diferencia, buscados en **cuatro pasadas de la más estricta a la más laxa**:
  1. **suma firmada == diferencia** — la explicación limpia;
  2. **|suma| == |diferencia|** — el mismo importe con el signo al revés. Pasa
     cuando el sistema contable lleva la cuenta del otro lado, y el back office
     igual necesita ver ese movimiento: **es** el movimiento, solo que el signo
     cuenta otra historia. Se marca (`signo_invertido`), no se disimula;
  3. **con MARGEN**, informando cuánto sobra (`resto`);
  4. **APROXIMADA** (`tolerancia_aproximada`, `max($100, 0,5%)`) — «sumando
     estos casi llegás». Ver más abajo.

  ⚠️ El margen nace de un caso real: la diferencia daba `1.176.659,79` y el
  movimiento que la explicaba era de `1.176.659,78` — **un centavo**. Con
  igualdad exacta el buscador contestaba «ningún movimiento da exactamente esa
  diferencia» y **escondía el movimiento que cualquiera reconoce de un vistazo**.

  **Es PROPORCIONAL a la diferencia, con piso**: `max($1, 0,001%)`. Un margen
  fijo no escala en los dos sentidos — sobre 500 millones, un peso es tan
  estricto como la igualdad exacta y vuelve a esconder el movimiento; sobre mil
  pesos, un porcentaje solo tampoco alcanzaría para un centavo. Es el mismo
  `rtol + atol` con que se comparan flotantes en cualquier lado. El porcentaje es
  deliberadamente minúsculo (un peso cada 100.000): alcanza para redondeos y no
  para hacer pasar un movimiento por otro. **El margen usado se muestra en la
  pantalla** — un criterio que decide qué aparece no puede vivir escondido en el
  código.

  El orden de las pasadas importa: buscando con margen desde el principio, «esto
  es» y «esto se le parece» valdrían lo mismo. Y **lo que sobra se informa
  siempre** — una explicación que tapa un resto cierra el caso con plata sin
  justificar adentro.

  ⚠️ **Pero se informa EN EL CANDIDATO, no como párrafo de aviso.** El signo
  invertido y el resto son campos de cada explicación (`signo_invertido`,
  `resto`) y la pantalla los marca al lado del movimiento EXACTO al que le pasan.
  Los dos avisos de texto que decían lo mismo arriba **se eliminaron**: eran un
  cartel genérico sobre TODAS las opciones para algo que le pasa a UNA, y lo que
  lograban era hacer dudar de si el problema era ese candidato o el otro. La
  marca puntual dice más y ocupa menos.

  Si la búsqueda se cortó, la respuesta lo dice (`candidatos_truncados`): «no
  encontré» y «no busqué todo» son cosas distintas.

### CALZAR POR IMPORTE (2026-08-21)

Un botón en el detalle, al lado de MOVIMIENTOS/CONSOLIDADO. Empareja los
movimientos de los dos lados que tienen el **mismo importe** y, prendido, los
esconde: lo que queda es la lista corta de **lo que no coincidió con nada del
otro lado**.

⚠️ **Por importe y nada más.** Las leyendas de los dos lados no se parecen y
cambian todo el tiempo (`TRANSFERENCIA ENTRE CUENT` contra `[Op. 1136612] bco a
bco`): cruzarlas por texto es imposible. Lo único que significa lo mismo de los
dos lados es el número — y el signo, que está alineado (plata que entra al banco
es Debe en el mayor).

⚠️ **Uno a uno.** Si el mismo importe aparece 3 veces de un lado y 2 del otro se
calzan 2 pares y queda 1 suelto. Calzar «el grupo contra el grupo» taparía justo
el movimiento que falta.

Consecuencias, y son el punto:

- **Los movimientos calzados salen de la búsqueda de explicaciones.** Uno que
  tiene su igual del otro lado ya está registrado en los dos sistemas: no puede
  ser el que falta. No es cosmético — es lo que deja el universo chico y hace
  que aparezcan las combinaciones largas.
- **La resta de los dos totales «sin calzar» ES la diferencia de saldos** (los
  pares se cancelan entre sí). Por eso el total del pie cambia cuando el filtro
  está prendido, y el rótulo también: dos números distintos no pueden llamarse
  igual.
- Los movimientos que son **gasto bancario** se marcan `imp` en la lista, con el
  mismo criterio y las mismas reglas que la columna GASTOS
  (`_gastos_de_movimientos`). Un descalce en un impuesto es **esperable** —el
  banco lo cobra hoy y contabilidad lo registra después— y no vale lo mismo que
  un descalce en una transferencia. Se marca para poder saltearlo, no se esconde.

### Combinaciones largas y explicaciones APROXIMADAS (2026-08-21)

⚠️ Caso real: una diferencia de `4.256.787,71` que salía de **sumar varios
movimientos del banco**, y la pantalla contestaba «ningún movimiento llega a esa
diferencia». Dos causas, las dos arregladas:

1. **`MAX_COMBINAR` era 3 y la explicación tenía más partes**: ni se generaba.
   Ahora es **8**, y el algoritmo dejó de ser `combinations()` —enumerar C(40,8)
   son 76 millones de combinaciones— para pasar a un **DFS con poda**
   (`_combinaciones`): ordenado de mayor a menor, el árbol se corta apenas la
   suma que queda disponible no alcanza o ya se pasó. `TOPE_NODOS` es el techo de
   trabajo; si se llega, la respuesta lo dice.
2. **No había forma de decir «casi»**. La cuarta pasada publica la mejor
   combinación dentro de `max($100, 0,5%)`, marcada `aproximado` y con el
   `resto` a la vista. Dos candados para que esto no se convierta en «encontrar
   cualquier cosa»:
   - **solo COMBINACIONES** (2 movimientos o más): un movimiento suelto que
     «casi» da es OTRO movimiento — para el redondeo ya está `tolerancia()`. Sin
     este candado, `999.950` pasaría por una diferencia de `1.000.000`, que es
     exactamente lo que se decidió no hacer;
   - **solo si ninguna pasada anterior encontró nada**.

Además, **el conjunto entero de gastos del día sin calzar** se publica como
candidato explícito cuando ninguna búsqueda dio una explicación exacta: son ~15
movimientos chicos, `_buscar` nunca podría combinarlos, y es el caso más común de
«falta en el mayor» (el banco cobra comisión, IVA y ley 25.413 el mismo día y el
sistema contable los registra al mes). Viaja con `motivo`.
- ⚠️ **Si los dos saldos coinciden al invertir el signo del mayor, se AVISA y no
  se corrige.** Significa que no falta ningún movimiento: la cuenta está del otro
  lado (acreedor/deudor). Invertir un signo por nuestra cuenta es exactamente
  cómo se fabrica una conciliación que miente.
- Es un `POST` que **no escribe nada** (el archivo va en el cuerpo porque no
  entra en una query string) y **no persiste nada**. El test de seguridad lo
  declara en `POST_QUE_NO_ESCRIBEN`, una lista corta y explícita: sumar uno ahí
  es el momento en que alguien tiene que justificar por qué no escribe.

### El CIERRE SELLADO — un dato, no un cálculo (2026-08-27)

**La regla, y es toda:**

```
SALDO AL CIERRE(F)   =  saldo del banco(F)  +  movimientos manuales de F
SALDO AL INICIO(F)   =  SALDO AL CIERRE(F−1), leído de la tabla
```

⚠️ **EXCEPCIÓN — las cuentas con `bancos.cuentas.origen = 'manual'`.** Esas no
las informa Interbanking: su saldo del banco sería siempre 0. Ahí el ajuste es
**ACUMULADO**, o sea que el saldo ES la suma de todo lo cargado a mano hasta esa
fecha:

```
SALDO AL CIERRE(F)   =  Σ movimientos manuales hasta F      (solo origen='manual')
```

Un `+1000` deja el saldo en 1000 ese día y **todos los siguientes**; cuando
después entra un `−900`, pasa a 100 y sigue así. Verificado corriendo el código:

| Fecha | Manual del día | Saldo al cierre |
|---|---:|---:|
| 24/08 | — | — |
| 25/08 | +1.000 | **1.000** |
| 26/08 | — | **1.000** |
| 27/08 | −900 | **100** |

En las demás cuentas el ajuste es **del día**: el saldo que informa el banco ya
trae adentro los movimientos de días anteriores, y sumarlos otra vez los
contaría dos veces. Congelado por test (`test_interbanking_tablero.py`), las dos
mitades.

> «el saldo al cierre es el saldo al inicio del otro día, no pueden tener fuentes
> distintas de lectura, es el mismo dato»
>
> «en conciliar el saldo al inicio tiene que tener un único laburo: ver cuál es
> el saldo al cierre del día anterior y ponerlo ahí»

`bancos.cierres_diarios` (PK `cuenta_id, fecha`) guarda ese valor.

⚠️⚠️ **UNA función arma el saldo al cierre: `_saldos_banco()`.** La usa la
pantalla principal para dibujar la columna Y la usa `sellar_cierre()` para
guardarlo. **No pueden dar distinto.** Eso fue un bug real: el consolidado tenía
su propia fórmula y el sellado otra, así que el número que el back office miraba
y daba por bueno no era el que al día siguiente se leía como SALDO INICIO.

Y `saldo_inicio` no calcula nada: es un `SELECT saldo FROM bancos.cierres_diarios
WHERE fecha = <día hábil anterior>`.

#### La tabla es DINÁMICA

Si mañana se carga un movimiento manual con fecha del 20, el cierre del 20 cambia
y **se vuelve a sellar solo**. Los tres puntos donde se actualiza:

| Cuándo | Dónde |
|---|---|
| se carga un manual | `crear_movimiento_manual()` → `sellar_cierre(fecha)` |
| se borra un manual | `borrar_movimiento_manual()` → `sellar_cierre(fecha)` |
| llega data de Interbanking | `jobs/interbanking_sync` (antes de purgar) |

Más el sellado **al vuelo** si una pantalla pide un día que no está sellado, y
`python -m scripts.sellar_cierres` para sembrar el histórico. Re-sellar pisa el
valor anterior: correrlo de más no rompe nada.

#### Los tres intentos, porque cada uno falló distinto

| Intento | Regla | Cómo falló |
|---|---|---|
| 1 | manual solo el día de la carga, apertura recalculada | la apertura del día siguiente no traía el manual: **la plata desaparecía** |
| 2 | Σ de TODOS los manuales | el saldo del banco ya los traía adentro → **contados dos veces**, el cierre daba de más |
| 3 (hoy) | el cierre se **sella** y la apertura lo **lee** | — |

El síntoma del intento 2 es el que hay que recordar: el cierre daba MÁS que
«Interbanking + los manuales del día», y la diferencia entre dos días no se
explicaba con nada de lo que la pantalla mostraba.

**Costo**: el consolidado pasó de 11 a 15 queries en el peor caso (14 en régimen;
el sellado al vuelo corre una vez por fecha).

### El umbral, el signo, y no cruzar lados

- ⚠️ **Debajo de UN PESO nominal no hay diferencia.** Caso real: `590.708,27`
  contra `590.708,12` — **15 centavos** que la pantalla mostraba como hallazgo y
  mandaban al back office a buscar un movimiento inexistente. Ningún movimiento
  puede explicar 15 centavos: es redondeo del sistema contable. Es **nominal y
  por moneda** (1 peso, 1 dólar), no un porcentaje — la unidad mínima de plata no
  escala con el tamaño de la cuenta.
- **El SIGNO dice de qué lado está el problema.** `diferencia = nuestro − mayor`:
  · **positiva** → el banco tiene más: **falta un movimiento en el mayor**
    (hay que cargarlo);
  · **negativa** → el mayor tiene más: **sobra un movimiento en el mayor**
    (hay que sacarlo).
  Por eso se busca en **los dos lados** y cada explicación dice de cuál salió: no
  es lo mismo «cargá esto en HYGIRUS» que «sacá esto de HYGIRUS», y una pantalla
  que solo dice «hay una diferencia de X» no le sirve a nadie.
  **Y va escrito arriba, al lado del número DIFERENCIA**, no solo abajo en cada
  opción: leer el signo obliga a acordarse de la convención, y el que abre el
  modal tiene que saber de una si el movimiento se carga o se saca.
- ⚠️ **Nunca se cruzan movimientos de los dos lados.** Una explicación que mezcla
  uno del banco con uno del mayor no es una explicación: **es una coincidencia
  aritmética**. Lo que se busca es concreto —«a este mayor le falta ESTE
  movimiento»— y eso vive entero de un lado. Congelado por test.

## MOVIMIENTOS A CONCILIAR

Lo que el back office **confirmó** en CONCILIAR y hay que arreglar.

⚠️ **Encontrar el movimiento no alcanza**: el arreglo se hace en **otro sistema
(HYGIRUS) y en otro momento**. Sin anotarlo, la próxima conciliación vuelve a
encontrar lo mismo y nadie sabe si ya se corrigió — así es como un hallazgo se
convierte en trabajo repetido.

- Cada fila dice **qué hacer**, no qué se detectó: `falta_en_el_mayor` →
  **cargarlo**; `sobra_en_el_mayor` → **sacarlo**. El que lo abre mañana necesita
  saber qué toca, no qué se diagnosticó.
- La **descripción se guarda tal como viene de SU lado**: si sobra en el mayor,
  como la escribe HYGIRUS (`[Op. 1131723] Extracción…`); si falta, como la
  escribe el banco (`CREDITO POR DATANET`). Es lo que la hace **encontrable en el
  sistema donde hay que ir a arreglarla** — traducirla sería obligar a buscar a
  ciegas.
- **Sin filtro de fecha**: un pendiente puede tardar días, y esconderlo al día
  siguiente sería perder justo lo que se quiso anotar.
- Lo resuelto **se marca, no se borra**: es la traza de qué se corrigió y quién.
  Borrar existe solo para lo confirmado por error, y pide confirmación.
- `bancos.conciliacion_pendientes`, con `UNIQUE (cuenta, fecha, acción,
  descripción, importe)`: confirmar dos veces el mismo movimiento es el mismo
  pendiente. **No la purga la retención de 3 fechas.**

## DIFERENCIAS — ¿el saldo se movió solo?

La cuenta que tiene que dar, por cuenta bancaria:

```
cierre(hoy) − cierre(día anterior)  ==  Σ movimientos de hoy
```

Lo que sobra es la **diferencia sin explicar**, y tiene una causa concreta que el
back office ya conocía: **el banco a veces registra un movimiento con fecha de
ANTEAYER que recién impacta en el saldo de AYER**. El movimiento queda en un día
que nosotros ya cerramos y el salto aparece en el otro.

### La propiedad que hace útil a la pantalla

Cuando el día cierra bien contra sus propios movimientos vale
`Σ movimientos = cierre(hoy) − apertura(hoy)`, y entonces:

```
sin_explicar = (cierre_hoy − cierre_ayer) − (cierre_hoy − apertura_hoy)
             =  apertura_hoy − cierre_ayer
```

O sea: **la diferencia ES el salto entre el cierre de un día y la apertura del
siguiente**, los dos informados por el banco. Por eso se publican las **dos**
lecturas —`sin_explicar` (el número) y `salto_apertura` (la evidencia)— y la
pantalla muestra las dos columnas: no dice solo cuánto falta, **dice dónde
mirar**. Si las dos no coinciden, el problema no es el asiento retroactivo sino
que el día no cuadra contra sus propios movimientos, que es otro hallazgo — por
eso viaja también `cierra` y la fila lo marca.

### ⚠️ EL SIGNO DEL IMPORTE (el bug que rompió esta pantalla)

**`bancos.movimientos.importe` viene YA FIRMADO de Interbanking**: un débito
llega **negativo**. El código asumía que llegaba en valor absoluto y que el signo
lo ponía `tipo` (C/D), así que se lo aplicaba **por segunda vez**. Consecuencia:
la suma del día devolvía **los valores absolutos**. Medido contra una cuenta
real: los movimientos sumaban `−500,53` (exactamente la variación del saldo) y la
pantalla mostraba `1.612.340.349,01`, acusando una diferencia de 1.600 millones
que no existía. Los dos números coinciden al centavo con las dos sumas, así que
no hay duda de cuál era el error.

La regla que queda, en `_firmado`, y vale para **todo el módulo**:

```
abs(importe) con el signo que dice `tipo`   ·   NUNCA `importe` crudo para sumar
```

No es "sacar la negación de más": es la única fórmula que da bien **tanto si el
banco firma el importe como si no**, y eso importa porque son nueve bancos
distintos y no hay ninguna garantía de que todos hagan lo mismo. Congelado por
test con los importes reales del caso.

De paso arregla dos cosas que se veían y nadie había levantado: los débitos se
dibujaban con **dos menos** (`--86,73`), porque la pantalla agregaba el signo a
un número que ya lo tenía; y la columna **GASTOS BANCARIOS salía en negativo**,
cuando la pregunta que contesta es *cuánto se llevó el banco*. Por eso el importe
se **publica en valor absoluto** y el signo lo dice `tipo`: un solo lugar decide.

### Decisiones que evitan diferencias falsas

- **Se reconcilia contra el BANCO, no contra la pantalla.** Los movimientos
  manuales mueven el saldo que mostramos pero no existen para el banco: si
  entraran, **cada ajuste nuestro aparecería como una diferencia del banco**. Se
  publican aparte (`ajuste_manual`) y la fila los marca, para que nadie se
  confunda al comparar con el consolidado.
- **Los IGNORADOS sí entran.** Ignorar saca un movimiento de los GASTOS, no del
  extracto: acá se está reconstruyendo la aritmética del banco.
- **Sin alguno de los dos cierres NO se inventa una diferencia.** «No sabemos» no
  es «no se movió»: asumir cero daría una diferencia del tamaño del saldo entero
  y mandaría al back office a buscar un movimiento que no existe. Esas cuentas
  dicen **«sin dato»** y se cuentan aparte para que no se lean como un verde.
- **El cierre sale de las mismas fuentes y en el mismo orden que el consolidado**
  (extracto → `bancos.saldos`). Si acá eligiera distinto, dos pantallas dirían
  dos saldos para el mismo día.
- El **día anterior** es la fecha más reciente anterior a la elegida que exista
  en la base — no "T−2 de calendario". Un feriado o un fin de semana largo no
  rompen la comparación. Si no hay ninguna, lo dice en vez de comparar contra
  nada.

La pantalla arranca mostrando **solo las cuentas con diferencia**, con un
`VER TODAS` al lado: una lista de 38 filas en cero esconde las 2 que importan.
Respeta el filtro por banco de la vista.

## Changelog

- **2026-09-01** — ⚠️ **La DESCRIPCIÓN que se ve y la que se matchea eran datos
  distintos.** El back office cargó `NOTA DB` en el balde IIBBPERCEP sobre el
  campo DESCRIPCIÓN, el movimiento ya estaba contado como gasto, y el desglose lo
  seguía dejando en MOVIMIENTOS RESTANTES.
  · **La causa**: la columna DESCRIPCIÓN de la pantalla es
    `descripcion_banco OR descripcion_ib` (`_movimiento_publico`), y el matcher
    miraba `descripcion_banco` **a secas**. Cuando el banco no manda su
    descripción —Credicoop con los `NOTA DB`, código `854`— la vista dibuja el
    CONCEPTO ahí, el operador lee `NOTA DB` bajo DESCRIPCIÓN, carga esa grafía
    sobre DESCRIPCIÓN (**el campo que además viene elegido por defecto en el
    ABM**) y el motor la compara contra un string **vacío**.
  · **Por qué no se veía**: no falla nada. Las dos mitades son coherentes consigo
    mismas, el total de gastos sigue dando bien, y la única evidencia es una
    columna en cero — indistinguible de «hoy no hubo ese impuesto». Es la
    **REGLA #9** (identidad ≠ nombre, dos copias sin árbitro) aplicada a un campo
    derivado: si la pantalla DERIVA, el motor tiene que derivar igual.
  · **El arreglo**: `bancos.valor_campo()`, la **única puerta** por la que
    `_matchea` (¿es gasto?) y `desglosar` (¿qué balde?) leen un campo. Es
    **estrictamente aditivo** — donde `descripcion_banco` trae texto, el fallback
    ni se consulta. Medido antes de aplicarlo sobre las 3 fechas de la base:
    **1 movimiento cambia de balde** (presentación) y **0 cambian de ser o no
    gasto** — no mueve un peso del total.
  · **Congelado por test** (`test_interbanking_gastos.py`): el más importante no
    prueba el fallback sino que **`_movimiento_publico` y `valor_campo` no se
    puedan volver a separar**. Los tres nuevos fallan si se saca el fallback.
  · Queda `scripts/diag_desglose_texto.py`: contesta «¿mi grafía agarró algo?»,
    que ninguna pantalla contesta, y lista las que no agarran nada.

- **2026-08-19 (8)** — **Se reparte la barra.** Siete controles en un renglón y
  cada píxel compite con el siguiente: se saca el título «Consolidado Bancos»
  (la solapa ya lo dice y costaba el ancho de dos botones), el **filtro de banco
  pasa a la izquierda** junto a la última actualización —es CONTEXTO de lo que se
  mira, no una acción— y **CONCILIAR + MOVIMIENTOS A CONCILIAR van encuadrados
  juntos**, porque son las dos mitades del mismo circuito (encontrar la
  diferencia y anotar qué arreglar) y el recuadro lo dice sin una palabra.
  Renombres: REPORTE FINAL → **REPORTE FIN DE DÍA**, DIFERENCIAS →
  **DIFERENCIAS BANCARIAS**.

- **2026-08-19 (7)** — **MOVIMIENTOS A CONCILIAR** (ver arriba) + tres reglas que
  faltaban en CONCILIAR: **umbral nominal de $1** (15 centavos no son un
  hallazgo), **el signo dice de qué lado está el problema** (falta o sobra en el
  mayor) y **nunca se cruzan movimientos de los dos lados**. Visual: las tablas
  de los dos lados se comprimen (la descripción se lleva el sobrante y el importe
  queda pegado) y el modal va a 1600px.

- **2026-08-19 (6)** — **Se saca el chequeo de «el mayor no cierra»** (gritaba en
  un archivo perfecto: el saldo inicial se leía mil veces más grande por la
  ambigüedad de los 7 decimales del formato), el **margen pasa a ser
  proporcional** (`max($1, 0,001%)`, mostrado en pantalla) y el modal se
  **ensancha a 1600px**, que es lo que piden dos tablas lado a lado.

- **2026-08-19 (5)** — **CONCILIAR muestra los dos detalles** (banco y mayor, uno
  de cada lado, solo descripción e importe) y **encuentra la explicación aunque
  no sea exacta**. Lo pidió un caso real donde la diferencia daba `…,79` y el
  movimiento `…,78`: por un centavo el buscador contestaba «ningún movimiento» y
  escondía el que se reconocía de un vistazo. Ahora busca en tres pasadas
  (exacto → valor absoluto → tolerancia de $1) y **siempre informa el resto**. De
  paso, el detalle del mayor **se auto-verifica**: saldo inicial + movimientos
  tiene que dar el saldo final del archivo, y si no da, lo canta.

- **2026-08-19 (4)** — **CONCILIAR: se escribe el BACKEND que faltaba.** El
  frontend se había mergeado solo y la vista tiraba «Not Found» (el 404 de
  FastAPI) — media feature mergeada es peor que ninguna. Ver la sección de
  arriba: el formato del saldo (`D`/`A` = signo del `numFmt`) quedó **medido**
  sobre el export real y congelado por 27 tests.

- **2026-08-19 (3)** — **No se puede borrar una columna del desglose con textos
  cargados** (ver arriba). Nació de perder COM.TRANSF y sus tres textos de un
  clic, sin poder recuperarlos porque la auditoría de la baja guardaba la mitad.
  Ahora la baja audita el balde COMPLETO y la columna cargada se desarma texto
  por texto, cada uno con su registro y su confirmación.

- **2026-08-19 (2)** — ⚠️ **El importe viene FIRMADO del banco** y el código le
  aplicaba el signo otra vez: las sumas devolvían los valores absolutos. Lo
  destapó DIFERENCIAS acusando 1.600 millones en una cuenta que cerraba perfecto.
  Se arregla en la raíz (`_firmado` = `abs(importe)` + `tipo`, una sola regla para
  todo el módulo) y de paso caen dos síntomas viejos: los débitos se dibujaban
  con dos menos y GASTOS BANCARIOS salía en negativo. **La columna de gastos
  cambia de signo**: ahora un cobro del banco suma positivo, que es lo que la
  pregunta pide.

- **2026-08-19** — **DIFERENCIAS** (ver la sección de arriba): botón nuevo y
  `GET /diferencias`. Es de solo lectura y no persiste nada — se calcula sobre lo
  que ya está en `bancos.*`. Cuesta 6 queries, pero se abre a demanda y no
  pollea, así que no entra al presupuesto de la vista.

- **2026-08-19** — **El ORDEN de las columnas se mueve desde la pantalla** (▲▼) y
  `POST /gastos/desglose/orden`. Lo pidió un pisón real: `IVA PERCEPCION RESOL
  GRAL` llega con el concepto en `IVA` y se lo comía la columna IVA. **No se
  agregó una excepción**: se agregó poder mover el orden, que es el mecanismo que
  ya estaba y solo faltaba exponer. Ver la sección de arriba.

- **2026-08-18 (14)** — **Se ELIMINA la foto del día** + **filtro por banco**.
  · La foto (`bancos.snapshots`, `POST /foto`, el botón SACAR FOTO y el fallback
    del consolidado) se sacó a pedido del back office el mismo día que se hizo:
    no la usaron. Se borra ENTERA en vez de dejarla apagada — un botón que nadie
    toca igual hay que mantenerlo, y una tabla que nadie lee confunde al que lea
    el esquema mañana (REGLA #5). Si el `apply_schema` alcanzó a crear
    `bancos.snapshots` en prod, se puede borrar a mano: no queda una sola línea
    de código que la lea.
  · **Filtro por banco** en la barra, client-side sobre lo que ya trajo el
    consolidado: pedirle la vista filtrada al backend sería un request por cada
    cambio de selector para esconder filas que ya están en memoria. Alcanza
    también al REPORTE FIN DE DÍA —el reporte no puede decir algo distinto de la
    pantalla desde la que se abrió— pero **no** al alta de movimientos manuales,
    que es una herramienta de carga: no poder cargarle un movimiento a un banco
    por tener la vista filtrada sería una trampa.
  · El botón pasa a decir **REGISTRAR MOVIMIENTOS MANUALES**.

- **2026-08-18 (13)** — **Bancos y movimientos MANUALES** (ver la sección de
  arriba) + **COPIAR IMAGEN** del reporte y la firma «Hecho en ACAQuant».
  · `bancos.cuentas.origen` y `bancos.movimientos_manuales`. 5 endpoints nuevos
    bajo `/manual/*` (4 escriben, 1 lee), con la misma allowlist y auditados; el
    proxy de Next suma `manual` a su lista de escrituras permitidas.
  · Los topes del test de queries pasan de 10/9 a **11/10**: los manuales
    impactan el saldo al cierre, así que no hay forma de armar el consolidado sin
    leerlos.

- **2026-08-18 (12)** — **REPORTE FIN DE DÍA**, y una pasada de prolijidad.
  · **REPORTE FIN DE DÍA** (ver arriba) — 100% front, sobre los datos que la vista ya
    tiene: no cuesta ni una query. Partido en bloques de 5 bancos (`BANCOS_POR_BLOQUE`):
    se probaron **las dos matrices** (bancos en las columnas, después bancos en
    las filas) y las dos quedaron con el 90% de las celdas vacías: los datos no
    son un producto cartesiano. Terminó en **una tabla por banco**, empaquetadas
    con los bancos grandes primero y con el modal ajustándose al contenido.
  · **Títulos**: «Reglas para contabilizar Gastos Bancarios» y «Desglose para
    contabilizar Impuestos». El botón dice qué contabiliza cada cosa, que es la
    pregunta real — «reglas» y «desglose» a secas no distinguen una de otra.
  · **El ABM del desglose pasa a TABLA horizontal** (# · columna · dónde · total
    del día · textos que suman · acciones) y el alta sale **inline en la misma
    fila**: antes aparecía debajo y cada apertura estiraba el modal hacia abajo,
    empujando las demás columnas fuera de la pantalla. Se sacó el párrafo de
    ayuda: ahora vive en el **«?»** del título. Cinco renglones de prosa que
    nadie lee no son ayuda, son ruido. Y «grafía» pasó a **«textos que suman»**.
  · **MODO OSCURO**: los modales usaban `--t-border` (#1a1a1a) sobre el panel
    (#080808) — un borde que no se ve. Toda la estructura de los modales pasa a
    `--t-border-2` (#2a2a2a en oscuro, #aab6c9 en claro) y las bandas de
    encabezado a `--t-surface-2`. Sin eso, la tabla se leía como un bloque de
    texto sin delimitar.
  · La respuesta del consolidado sube ENTERA a la barra (un `onDatos` en lugar de
    tres callbacks sueltos): la barra necesita la sync, la presencia, si es foto y
    los bancos para el reporte, y cada dato nuevo agregaba un prop y una copia de
    estado que se podía quedar vieja.

- **2026-08-18 (11)** — **El DESGLOSE deja de ser código y pasa a ser catálogo
  editable** (ver arriba) + **la DESCRIPCIÓN sale entera**.
  · Motivo: apareció un impuesto que ningún balde agarraba. Con los baldes en una
    constante, sumarlo era un commit y un deploy — el equipo tenía que pedirlo y
    esperar. Ahora hay ABM (botón **DESGLOSE** del modal): columnas, grafías,
    grupo y orden. 4 endpoints nuevos, todos bajo `/gastos/*` (el proxy de Next no
    tuvo que ampliar su superficie), con la misma allowlist y auditados.
  · La pantalla muestra **el total del día al lado de cada columna**: después de
    agregar una grafía se ve el número moverse sin salir del modal. Es la
    comprobación de que agarró.
  · **DESCRIPCIÓN entera**: la columna se lleva el sobrante de ancho (`w-full` en
    su `<th>`, el mismo truco que hizo entrar entera la CUENTA del consolidado).
    Con 10 columnas el reparto parejo dejaba angosto justo el dato más largo.
    ⚠️ Si igual se lee cortada, el corte lo hizo el BANCO: `code_description_bank`
    llega truncado a ~25 caracteres y con los acentos rotos — por eso las grafías
    van por `contiene` sobre la raíz de la palabra y nunca por `igual` sobre el
    texto completo.

- **2026-08-18 (10)** — **IGNORAR un movimiento** (ver la sección de arriba) +
  **repaso de queries**, ahora congelado por test.
  · `vista` leía los movimientos crudos y el catálogo de reglas **dos veces cada
    uno**: 4 queries donde alcanzaban 2. Es la forma en que esto crece sin que
    nadie lo note — se agrega un campo a la respuesta llamando otra vez a la
    función que ya lo trajo, y nadie ve 8ms. `tests/unit/test_interbanking_queries.py`
    fija los topes (`consolidado` 9 · `vista` 8) y falla si una query se repite
    idéntica en el mismo request. Subir el número tiene que ser una decisión que
    alguien tome a mano.
  · Por eso mismo la marca de ignorado **NO es una query nueva**: viaja por
    `LEFT JOIN` en las dos queries de movimientos que ya corrían. Contra Supabase
    el peaje es de ~8,5ms por *roundtrip* y es 100% distancia — una columna más
    en una query que ya corre es gratis, una query más no.

- **2026-08-18 (9)** — **AUDITOR del desglose** (ver arriba) y **COM.TRANSF suma
  las tres grafías**. El campo pasó del BALDE al MATCHER para que un balde pueda
  mirar `descripcion_ib` y `descripcion_banco` a la vez.

- **2026-08-18 (8)** — **DESGLOSE de los gastos** (ver la sección de arriba).
  El CONSOLIDADO pierde **SALDO AL INICIO, VARIACIÓN y MOVS.** («no sirven») y
  gana **IVA · IVAPERCEP · IIBBPERCEP · COM.TRANSF · OTROS IMP**. El MODAL
  muestra el desglose completo en horizontal y pierde el contador de movimientos
  (la lista está abajo). Las etiquetas de las columnas las manda el BACKEND
  (`catalogo_desglose`): si el front las copiara, cambiar un balde obligaría a
  tocar dos lados y podrían quedar diciendo cosas distintas.

- **2026-08-18 (7)** — **GASTOS BANCARIOS: el modelo completo** (ver la sección
  de arriba) + **usuarios en línea** en la barra (`bancos.presencia`, el poll de
  60s es el heartbeat, TTL 180s — mismo patrón que Tesorería y SENEBIS).
  · La vista **deja de ser 100% read-only**: se suman 3 endpoints de escritura,
    todos sobre tablas NUESTRAS (`bancos.gastos_*`), todos detrás de la allowlist
    y todos auditados. **Hacia Interbanking se sigue sin escribir nunca** — esa
    es la línea que importa y el test del cliente GET-only queda intacto. El test
    del router ahora **ENUMERA** las tres escrituras permitidas: sumar una obliga
    a tocar el test, que es el momento en que alguien se pregunta si corresponde.
  · El proxy de Next deja pasar POST/PUT/DELETE **solo bajo `/gastos/*`**; una
    escritura contra cualquier otro path se rechaza ahí, antes de salir.
  · La marca se hace con un clic en la columna GASTO del modal, y hay un tercer
    estado (**↺ volver a la regla**) para deshacer: sin él, arreglar una marca
    equivocada obligaría a adivinar qué decía la regla y marcar el opuesto,
    congelando para siempre algo que la regla ya resolvía.

- **2026-08-18 (6)** — **La sub-tab «Detalle por cuenta» se ELIMINA y pasa a ser
  un MODAL** que se abre haciendo clic en la cuenta del consolidado. Era una
  vista aparte que obligaba a elegir banco y después cuenta en dos selectores,
  para ver el detalle de una fila que ya estabas mirando en la otra pantalla; el
  modal parte de donde estás. Es el mismo patrón que el modal por celda de
  Tesorería → BANCOS.
  · **No hizo falta un endpoint nuevo**: `GET /vista?cuenta_id&fecha` ya devolvía
    exactamente esto. Sí se sacó de ahí el listado de cuentas — el modal manda el
    `cuenta_id`, así que consultarlo era una query de más por cada apertura.
  · **Botón DESCARGAR** (.xlsx client-side, `lib/xlsx-export`, import lazy de
    SheetJS). El importe se exporta **FIRMADO** (débito negativo) para que la
    columna sume el neto del día en Excel sin que nadie arme la fórmula; y la
    SUCURSAL va como TEXTO porque viene `010` y como número perdería el cero.
  · La fila del consolidado tiene ahora **dos gestos**: clic en la CUENTA abre el
    detalle, clic en una celda de DATOS copia el valor. No es arbitrario — la
    cuenta es la identidad de la fila y "entrar" es la acción sobre ella; los
    datos son valores y lo que se hace con un valor es copiarlo. El número de
    cuenta se copia igual desde el encabezado del modal.
  · Al quedar una sola vista se sacaron las pills de sub-tab, y con ellas los
    componentes `Panel` y `Pill`, que quedaron huérfanos.

- **2026-08-18 (5)** — Tanda de pedidos del back office:
  · **La fecha por defecto pasa a ser el DÍA HÁBIL ANTERIOR a hoy**, no hoy. Es
    el día CERRADO: el banco ya informó su extracto completo y su saldo final;
    hoy a media mañana es una foto a mitad de camino.
  · **RETENCIÓN: `bancos.*` guarda solo las 3 FECHAS más recientes.** Purga en
    cada corrida (`FECHAS_A_MANTENER` en el job). El corte es por fecha DISTINTA
    y global, no por antigüedad en días — así un feriado o un fin de semana largo
    no vacía la tabla. **No se purga nunca si la corrida no trajo datos**: si
    Interbanking está caído, borrar igual dejaría la base con menos días de los
    que tenía, un borrado silencioso causado por el proveedor. Congelado por test.
  · **El NÚMERO DE CUENTA se publica ENTERO.** Antes salía solo la terminación
    (`…0488`) — era una decisión mía, no del user, y sobraba de prudente: son las
    cuentas de la casa, las mira el back office detrás de CF Access, y el número
    es justamente lo que copian a otros sistemas. **El CBU sigue sin salir**, y
    esa es la línea: el número IDENTIFICA, el CBU es lo que hace falta para
    TRANSFERIR. Dos riesgos distintos, no se relajan juntos.
  · **Clic en cualquier celda con dato = se copia al portapapeles.**
  · Se eliminó la franja de texto que explicaba cada cuánto corre el cron y
    cuántas cuentas no tenían dato. Queda **«Última actualización DD/MM/AAAA
    HH:MM»** en la misma línea de las pills. Una pantalla no se explica a sí
    misma en prosa.
  · Tablas: la cuenta a la IZQUIERDA, las columnas de datos CENTRADAS, de ancho
    parejo (`w-[13%]`) y con una línea que las separa. Sin ese ancho declarado
    la columna CUENTA se quedaba con TODO el sobrante y dejaba una franja en
    blanco enorme en el medio.

- **2026-08-18 (4)** — **La vista pasa a UN DÍA y se limpia el consolidado**
  (pedido del back office):
  · **Una sola fecha**, no `desde`/`hasta` — «es siempre el mismo día». Antes el
    consolidado mostraba la apertura de un día contra el cierre de otro, que no
    es la variación de nada. `/vista` y `/consolidado` toman `fecha`; el default
    (HOY en hora argentina) lo decide el backend y cae DENTRO de la ventana que
    ingesta el job, congelado por test.
  · **Fuera la barra de totales por moneda y los subtotales por banco.** No se
    usaban. El banco sigue agrupando las cuentas —eso es lo que hace navegable
    una lista de 38— pero cada fila se lee sola y la vista arranca en la tabla.
  · **Fuera PROY. 24HS / 48HS.**
  · **Columna GASTOS BANCARIOS**, hoy `null` en todas las filas: la regla de qué
    movimiento es un gasto **no está definida** (`GASTOS_BANCARIOS_CODIGOS`
    vacío en `api/services/bancos.py`; completarla es una línea y empieza a
    devolver números sin tocar nada más). Se muestra «—» y **nunca 0** — «no
    sabemos» y «no hubo gastos» son cosas distintas, y un cero se leería como
    que el banco no cobró nada. **Los candidatos hay que MEDIRLOS** (REGLA #2):
    `code_description_ib` toma 23 valores e incluye 'IMPUESTO AL DEBITO', que es
    un impuesto y no un gasto del banco.
  · La columna CUENTA entra **entera**: la tabla dejó de repartir el ancho por
    igual (`w-auto min-w-full` + `whitespace-nowrap`), que era lo que cortaba el
    nombre.
  · `deploy/deploy.sh`: el aviso de "este deploy tocó código de motores" pasa de
    un bloque que listaba motor por motor **con su comando de restart al lado**
    —que se leía como «reiniciá los motores», justo lo contrario de lo que el
    script hace— a **UNA línea**. El detalle se pide con `--motores-detalle`.

- **2026-08-18 (3)** — **MOVIMIENTOS pasa de 4 a 8 columnas.** El back office
  pidió CONCEPTO · COD OP · FECHA · COMPROBANTE · SUCURSAL · IMPORTE ·
  DESCRIPCIÓN · COD OP BCO. Medido en prod (2026-08):
  **las dos que faltaban de verdad (`branch_office_activity` y
  `operation_code_bank`) ya estaban GUARDADAS** en `bancos.movimientos` desde la
  primera corrida — el backend no las publicaba. Costo real: dos líneas en
  `_movimiento_publico`, **cero llamadas nuevas y cero backfill**. Otras dos
  (`codigo`, `comprobante`) ya viajaban en el JSON y no se dibujaban. Hallazgos
  del diag sobre 178 movimientos de 5 cuentas:
  · **Interbanking NO informa la hora** — `process_date`, `movement_date`,
    `value_date` y `real_date_activity` tienen **1 solo valor distinto** y es
    medianoche. La vista ahora dibuja la hora solo si no es `00:00:00`.
  · **`customer_cuit` / `depositor_description` llegan al 13%** — de 9 de cada 10
    movimientos NO se sabe la contraparte. Por eso no es columna propia.
  · Los códigos **`*_standard`** (la normalización cross-banco que promete
    Interbanking) llegan al **3%**: no sirven para clasificar.
  · **`associated_voucher` viene "lleno" pero con espacios en blanco** — un campo
    que parece tener dato y no tiene ninguno.
  · La API de Movimientos NO trae `statement_number` → Extractos sigue siendo la
    fuente, y las 4 exclusivas de Movimientos no justifican una llamada más.

- **2026-08-18 (2)** — **Se suma la API de SALDOS** (`bancos.saldos`, una llamada
  más por cuenta) y se **corrige de raíz la premisa del doc**. Dos cosas:
  (a) el consolidado ya no muestra «—» en la cuenta que no se movió: cae a
  `bancos.saldos`, declara en `fuente` de dónde salió cada número y publica
  `discrepancia` cuando el extracto y el saldo del banco no coinciden; se suman
  los proyectados 24/48hs. (b) **Se eliminó la sección "por qué importa" que
  fundaba todo en tapar agujeros de la vista Tesorería** — el user lo marcó como
  falso y se verificó: son objetos sin clave en común (cuenta operativa de Aunesa
  vs cuenta bancaria). Ver "ESTO NO SE MEZCLA CON TESORERÍA".
- **2026-08-18** — **La ventana pasa a días HÁBILES.** El back office reportó la
  tab vacía: era martes, el lunes 17 fue feriado y `ayer + hoy` pedía 17..18, dos
  días sin actividad bancaria; el "ayer" real era el viernes 14. El mismo bug
  ocurría todos los lunes. Se corrigió en los **dos** lugares donde vivía —la
  ventana de ingesta (`ventana()` en el job) y el rango por defecto de la vista
  (`bancos.rango_default()`, que usan `/vista` y `/consolidado`)— porque arreglar
  solo el job dejaba la pantalla igual de vacía. `--dias` ahora cuenta hábiles.
  Test nuevo: `tests/unit/test_interbanking_ventana.py`. **Se auto-repara**: la
  próxima corrida re-pide el último día hábil y levanta lo que hubiera quedado sin
  ingerir. También se actualizó el estado del doc, que decía que faltaba la tab del
  front (existe) y que el maestro de cuentas eran 2 llamadas (son 4).
- **2026-08-14** — Alta de la aplicación en el portal. `core/interbanking.py`,
  un diag de auth (ya borrado),
  `scripts/diag_interbanking.py` (el smoke, que se conserva). Detectado
  que el `tokenUrl` de los YAML del proveedor no es el endpoint real. **Auth
  resuelta y 26 cuentas leídas** contra producción.
