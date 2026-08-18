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
python -m scripts.diag_interbanking_auth   # PRIMERO: por qué falla el token
python -m scripts.diag_interbanking        # DESPUÉS: los datos reales
python -m scripts.diag_interbanking_raw    # el JSON crudo + qué campos vienen vacíos
```

Se pueden correr **desde cualquier PC**, no hace falta el Droplet: las APIs de
Interbanking son internet público y los diags de auth y de forma no tocan la base.
Lo único que necesita DB es el cruce contra `tesoreria_cuentas` del segundo diag,
que está escrito para saltearse solo si no hay conexión.

`diag_interbanking_auth` prueba en matriz endpoint × forma de mandar las
credenciales × con/sin scope, y de cada intento muestra el status, el header
`WWW-Authenticate` (que suele traer el motivo real cuando el cuerpo viene vacío)
y el cuerpo. Si ninguna funciona, sondea el gateway para distinguir
"credenciales mal" de "aplicación no habilitada" — son reclamos distintos al
proveedor.

`diag_interbanking` hace el smoke de las 5 APIs y cruza el listado de cuentas
contra `operaciones.tesoreria_cuentas`.

También hay una colección de Postman en `docs/postman/`.

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
   antes cambiaría dónde caen movimientos que hoy ya están bien clasificados.
3. **Cuesta UNA query por request** (baldes + matchers en un `LEFT JOIN`), y los
   topes del test de queries subieron de 9/8 a **10/9** a propósito. Es el precio
   de que el equipo no dependa de un deploy, y está medido: ~8,5ms.

El desglose se **deriva en la lectura**, así que un cambio se ve en el próximo
poll, sin recomputar nada y sin poder contradecir al catálogo. Y borrar un balde
no rompe nada: sus movimientos pasan a MOVIMIENTOS RESTANTES y **ningún total
cambia**.

⚠️ **Las dos trampas están explicadas DENTRO de la pantalla**, no solo acá:
`contiene` vs `es igual` (con `contiene`, «IVA» se come «IVAPERCEP» — el total
sigue dando bien y dos columnas quedan mal) y qué significa el orden. Un ABM que
deja meter la pata en silencio es peor que no tenerlo.

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

## La FOTO del día

⚠️ **Acá la foto NO existe por el mismo motivo que en Tesorería.** Allá la vista se
arma en vivo contra Aunesa y no se persiste, así que sin foto el día se pierde.
Acá el dato **sí** está en la base… pero `bancos.*` **retiene solo 3 fechas**: al
cuarto día el consolidado de un día cerrado desaparece. **La foto es lo que lo
hace durar** — por eso guarda **30 fechas** y no 3.

- Se congela **la respuesta de `consolidado()` tal cual**, no los saldos crudos.
  Si se guardaran los crudos, la foto y la vista podrían mostrar números
  distintos el día que cambie una regla de gastos. La foto **es lo que se vio**.
- **Lo que depende de quién mira no se congela** (`puede_escribir`, quién está en
  línea): mañana la foto la abre otro.
- **UNA foto por fecha**; re-sacarla pisa la del día y el historial de quién y
  cuándo queda en `bancos.gastos_audit`. El **TTL de 30 fechas se purga en el
  mismo INSERT**, no en un cron: así la tabla no puede crecer aunque el job de
  limpieza no exista nunca.
- `hash_sha256` del payload canónico detecta una edición hecha por fuera de la
  API. Si no da, **la foto se muestra igual** (el dato es el que hay) y la vista
  lo canta.

**Cuándo se usa**: `consolidado()` sirve la base mientras el día esté ahí. Solo
cuando **ninguna** cuenta tiene dato de ese día —o sea, cuando la retención ya lo
purgó— cae a la foto y lo declara en `es_foto`. Mientras el día viva en la base
manda la base: una foto vieja no puede tapar un dato corregido después. Por eso
la query extra se paga **solo en el caso perdido**, no en el camino normal.

## REPORTE FINAL

El saldo al cierre de **todas** las cuentas en una sola grilla, para pasar hacia
afuera. Es una **matriz**: una **columna por banco**, una **fila por cuenta**, y
en el cruce el saldo. Como cada cuenta pertenece a un solo banco, la grilla queda
escalonada — que es exactamente cómo se lee un reporte de posición bancaria y
cómo se pega en una planilla.

Dos separadores en blanco, y ninguno es decorativo:

- una **columna vacía** entre banco y banco;
- una **fila vacía** entre el bloque ARS y el bloque USD. Separar por moneda
  importa más que ordenar: sumar pesos con dólares en la misma corrida visual es
  el error que este formato evita.

El título de cada fila es **exacto** lo que dice la columna CUENTA del
consolidado (`CC ARS · 30010… · ETIQUETA`): si dijera otra cosa, el que compara
las dos pantallas tendría que traducir. El día es el **mismo** que muestra la
vista, así el reporte no puede decir algo distinto de la pantalla desde la que se
abrió. Cabecera en el azul de la casa con el logo: **se muestra y se captura**,
no es una pantalla de trabajo.

## Changelog

- **2026-08-18 (12)** — **FOTO del día + REPORTE FINAL**, y una pasada de prolijidad.
  · **SACAR FOTO** (ver arriba) — `bancos.snapshots`, 30 fechas, `POST /foto`. El
    proxy de Next suma `foto` a su lista de escrituras permitidas (era solo
    `gastos`).
  · **REPORTE FINAL** (ver arriba) — 100% front, sobre los datos que la vista ya
    tiene: no cuesta ni una query.
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
  DESCRIPCIÓN · COD OP BCO. Medido con `scripts/diag_interbanking_columnas`:
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
- **2026-08-14** — Alta de la aplicación en el portal. Colección de Postman
  (`docs/postman/`), `core/interbanking.py`, `scripts/diag_interbanking_auth.py`,
  `scripts/diag_interbanking.py` y `scripts/diag_interbanking_raw.py`. Detectado
  que el `tokenUrl` de los YAML del proveedor no es el endpoint real. **Auth
  resuelta y 26 cuentas leídas** contra producción.
