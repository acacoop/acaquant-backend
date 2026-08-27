# postman/ — colecciones importables

Tres colecciones para explorar a mano lo que los jobs consumen por código.
**Ningún archivo de acá lleva credenciales**: los environments vienen con los
campos vacíos y marcados como `secret`, y se completan una sola vez en Postman.

| archivo | qué es |
|---|---|
| `aunesa.postman_collection.json` | API del custodio (Irmo), con **login automático** |
| `aunesa.postman_environment.json` | plantilla del environment de Aunesa |
| `acaquant.postman_collection.json` | nuestra API, con las 3 capas de auth resueltas |
| `acaquant-prod.postman_environment.json` | plantilla del environment de producción |
| `byma-clearing.postman_collection.json` | BYMA Clearing Workflow (garantías + obligaciones), con **token automático** |
| `byma-clearing.postman_environment.json` | plantilla del environment de BYMA |

Importar: en Postman, **Import → Files** y seleccionar los archivos. Después
elegir el environment en el selector de arriba a la derecha y completar los
valores vacíos.

---

## 1 · Aunesa — login automático

El token deja de copiarse a mano. Un pre-request script a nivel **colección**
corre antes de cada request: si el token guardado tiene menos de 20 minutos no
hace nada, y si no, hace el `POST /login` con las credenciales del environment
y lo guarda. El request `0. Login` queda solo para debug.

Hay que completar tres valores (los mismos que el Droplet tiene en su `.env`
como `AUNESA_CLIENT_ID` / `AUNESA_USERNAME` / `AUNESA_PASSWORD`):

- `aunesa_client_id`
- `aunesa_username`
- `aunesa_password`

Las otras variables (`id_cuenta`, `desde`, `hasta`) son los parámetros con los
que jugás; ya vienen con un ejemplo cargado.

> **Fechas en `DD/MM/YYYY`**, no ISO. Y ojo con la **regla H1**: `desde = X`
> devuelve la posición liquidada al día hábil **anterior** a X. Para ver la
> posición de hoy hay que mandar el próximo hábil.

### Los parámetros que el job NO manda (2026-08-12)

`posicionValuada` acepta más de lo que el backfill le manda. Estos ahora son
variables del environment, para moverlos sin editar el request:

| variable | qué prueba |
|---|---|
| `por_concertacion` | por CONCERTACIÓN vs por LIQUIDACIÓN. **Es el que importa**: por liquidación, una caución que vence dentro de un mes ya está adentro del `Acumulado` y deja el ARS negativo HOY. |
| `estado` | `DIS` disponible · `GAR` garantía · `DIF` diferido. Vacío = todos. |
| `lugar` | `Local` u otros. Vacío = todos. |

El job diario manda solo `desde`, `hasta`, `tipoCuenta`, `nivel` y
`ocultarCerradas` — los tres de arriba nunca se probaron.

Para barrer las combinaciones de una sola vez, sin ir de a una en Postman:

```
python -m scripts.diag_caucion_saldo --cuenta 805
```

Devuelve una tabla variante × Acumulado de ARS/USD/USDC. La fila cuyo ARS
coincide con el saldo real es la respuesta.

### Lo que la posición valuada devuelve de verdad

La respuesta mezcla dos cosas, y se separan por el campo `informacion`:

- `informacion == "Acumulado"` → las **posiciones**. Es lo único que el job
  diario persiste en `portafolio.tenencia`.
- cualquier otro texto → un **movimiento pendiente**, con su fecha de
  liquidación en el campo `fecha`. Por ejemplo
  `"Venta [AO29] 2.197,00@143152 (ARS 24hs)"` o
  `"Caución tomadora ARS 3.143.854,00@25,75% (ARS 1 días) (Cierre)"`.

A medida que `desde` avanza aparecen **menos** movimientos: los que ya
liquidaron se absorbieron en el `Acumulado`. Es la mecánica completa del
endpoint y se ve clarísima cambiando `desde` en Postman.

---

## 2 · ACAQuant — las tres capas de auth

Producción exige **tres** cosas a la vez. El pre-request script de la colección
las arma solo; lo único que hay que hacer es cargar el environment.

| capa | cómo se pasa | variable |
|---|---|---|
| Cloudflare Access | headers `CF-Access-Client-Id` / `CF-Access-Client-Secret` | `cf_client_id`, `cf_client_secret` |
| API key de la app | `Authorization: Bearer …` | `acaquant_api_key` |
| Identidad | header `x-acaquant-user-email` | `acaquant_user_email` |

**Por qué hacen falta las tres.** Cloudflare tapa el dominio, así que sin
service token ni siquiera llegás a la app. La `API_KEY` es la capa EXT-AUTH1
(`api/deps.py::verify_api_key`). Y la identidad es la menos obvia: un service
token es identidad de **máquina** — el JWT que emite Cloudflare trae
`common_name` pero **no** trae email —, así que sin el header de email el
backend te resuelve como `service:<cn>`, que no tiene ningún módulo y te
devuelve 403 en todo lo gateado.

Ese header **solo se respeta si el `common_name` del service token está en
`CF_TRUSTED_SERVICE_TOKENS`** (allowlist del unit de systemd). Si no está, el
backend lo ignora a propósito y te deja en `anon`.

### Cómo saber si quedó bien: `GET /api/me`

Es el request de diagnóstico y conviene correrlo primero:

- devuelve tu email y una lista de módulos → las tres capas están bien;
- devuelve `anon` o `service:<algo>` → el header de identidad no se está
  tomando, casi siempre porque el service token no está en la allowlist;
- devuelve **HTML** en vez de JSON → no pasaste Cloudflare Access.

Ese último caso es el que más tiempo hace perder, porque no falla como un 401
sino como "la respuesta no es JSON". La colección tiene un test que lo escribe
en la consola de Postman con todas las letras.

### Local (dev)

El mismo par colección + environment sirve: poné
`acaquant_base_url = http://localhost:8000` y dejá **vacías** las tres
variables de Cloudflare. El script no manda esos headers, y con `ENV=dev` la
API no exige `API_KEY`.

---

## 3 · BYMA Clearing Workflow — token automático y freno de escritura

Gestión de garantías (collateral) y consulta de obligaciones de liquidación.
Entornos: **homologación** `hs-clearing-api.byma.com.ar`, **producción**
`clearing-api.byma.com.ar`. El environment viene apuntando a homologación.

### Lo único que hay que cargar

| variable | qué es |
|---|---|
| `byma_client_id` | usuario de la aplicación en el portal BYMA |
| `byma_client_secret` | su contraseña — lo único de verdad sensible |

Todo lo demás ya viene con el valor que corresponde.

> ⚠️ **El token de Clearing NO se pide donde dice el manual genérico.** Ese
> manual (el del Portal de Desarrolladores) indica
> `hs-api.byma.com.ar/oauth/token/`, que es el de las **otras** APIs (custodia).
> Clearing emite sus tokens en **su propio host**:
> `hs-clearing-api.byma.com.ar/oauth/token/`. Con el otro, la respuesta es
> **401** — verificado el 2026-08-14.
>
> **La fuente de verdad por API es el portal, no el manual.** Dentro del método
> que querés usar, en el apartado *Autenticación*, el botón **"¿Cómo obtener un
> token de acceso?"** genera los valores exactos para tu aplicación: endpoint,
> `client_id`, `client_secret` y **scope**. Ante cualquier duda, ese botón gana.

### Scopes: leer y escribir son permisos distintos

| scope | para qué |
|---|---|
| `clearing clearingworkflow.read` | consultar obligaciones (el default del environment) |
| `clearing clearingworkflow.create` | registrar depósitos y extracciones |

Si la aplicación no tiene habilitado `.create`, el token se emite igual pero los
dos POST devuelven **403**. Habilitarlo se pide a BYMA: el manual aclara que
*"las APIs de Custodia y BYMA Clearing con métodos de tipo POST/PUT requieren
homologación"*.

El token se renueva solo (TTL 30 min) con `client_credentials`. Prueba primero
con **Basic** y, si el servidor lo rechaza, reintenta mandando las credenciales
**en el body** — los dos métodos existen y BYMA no documenta cuál usa. El
request `0. Token` queda solo para debug: muestra el JWT descompuesto (emisor,
scopes, vencimiento).

### ⚠️ Los dos POST no son un test: registran una orden

`deposits` y `withdraws` mueven garantías de verdad. Están frenados por dos
llaves distintas del environment, y el freno vive en el pre-request script:

- `permitir_escritura = SI` — habilita los POST. Sin esto, no salen.
- `permitir_produccion = SI` — hace falta **además** si la URL no tiene el
  prefijo `hs-`.

El `clientId` **debe ser único por día y por agente**, así que lo genera el
script desde la secuencia `byma_op_seq`. Un intento fallido quema un número a
propósito: repetir un `clientId` es peor que saltearse uno.

`assetExternalRefDataSystemId` (especie) y `currencyId` (moneda) son
**mutuamente excluyentes** — por eso son dos requests distintos y no uno con
campos para borrar.

### Las dos trampas que el test script te canta en la consola

1. **Fuera del horario de servicio (08:00–00:00) BYMA responde HTTP 200** con
   `{"type":"OOS"}`. Un cliente que mire solo el status code guarda "Service Out
   of Service" como si fuera un dato bueno. Hay un test que se pone en rojo.
2. **Las cantidades vienen con tipo mixto**: en el mismo ejemplo de la doc,
   `pendingQuantity` aparece como `"-10360557"` (texto), `"-48351.000000"`
   (texto con decimales) y `0` (número). Si en la consola ves `TIPOS MIXTOS`,
   ese campo hay que tratarlo siempre como texto.

### Paginación

El test guarda el `bookmark` solo. Si quedan más páginas, alcanza con **volver a
mandar el mismo request**; al llegar a la última lo limpia. Si está vacío, el
pre-request **saca el parámetro** en vez de mandarlo en blanco (un `bookmark=`
vacío no es "sin bookmark": es un cursor inválido).

### Lo que la doc de BYMA se contradice, acá es una variable

| variable | por qué existe |
|---|---|
| `byma_sufijo` | los `curl` de la doc usan `.json`, el OpenAPI no. Default `.json`; vaciala para probar la otra forma. |
| `byma_cuentas_csv` | `accountIds` figura como lista en la doc y como valor único en el OpenAPI. Probar `14,15` para ver si toma varias. |

Los 5 `servers` del OpenAPI tienen **las etiquetas cruzadas** ("Production
principal" apunta a `hs-`, que es homologación; "Production" apunta a
`localhost`). No te guíes por esos nombres.

> Para automatizar después —paginación completa, correr desde el Droplet— está
> `python -m scripts.diag_byma_clearing`, que hace lo mismo por línea de
> comandos y comparte las mismas variables de entorno.

---

## Seguridad

⚠️ **Un service token que puede afirmar `x-acaquant-user-email` puede hacerse
pasar por cualquier usuario de la plataforma.** Está acotado por la allowlist,
pero en la práctica es una llave de admin: conviene que sea un token
**dedicado a Postman**, con nombre propio para poder revocarlo solo, y no
reusar el del frontend.

Las credenciales se cargan **en Postman**, nunca en estos archivos. Si alguna
vez exportás un environment con valores adentro, no lo commitees.

Los requests de estas colecciones son de **lectura**. El backfill de aranceles
(`POST /aunesa/boletos/backfill`) se dejó afuera a propósito: escribe.



---

## 4 · API externa (accionistas)

**La colección de `/ext` NO vive acá** (decisión del user, 2026-08-27): es material
que se le entrega al consumidor externo, no una herramienta nuestra, y en el repo
sólo agregaba una copia más para mantener sincronizada a mano.

El contrato es **auto-generado desde el código y no puede quedar viejo**:

```bash
python -c "import json;from api.ext.app import ext_app;print(json.dumps(ext_app.openapi(),indent=2,ensure_ascii=False))" > acaquant-ext-openapi.json
```

Ese JSON se importa directo en Postman (**Import → File**) y arma la colección
sola, con todos los endpoints y parámetros al día. Doc del dominio:
`docs/API_EXTERNA.md`.
