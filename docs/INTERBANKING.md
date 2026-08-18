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

## Changelog

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
