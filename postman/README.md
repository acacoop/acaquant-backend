# postman/ — colecciones importables

Dos colecciones para explorar a mano lo que los jobs consumen por código.
**Ningún archivo de acá lleva credenciales**: los environments vienen con los
campos vacíos y marcados como `secret`, y se completan una sola vez en Postman.

| archivo | qué es |
|---|---|
| `aunesa.postman_collection.json` | API del custodio (Irmo), con **login automático** |
| `aunesa.postman_environment.json` | plantilla del environment de Aunesa |
| `acaquant.postman_collection.json` | nuestra API, con las 3 capas de auth resueltas |
| `acaquant-prod.postman_environment.json` | plantilla del environment de producción |

Importar: en Postman, **Import → Files** y seleccionar los cuatro. Después
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
