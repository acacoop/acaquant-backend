# Postman — validar las APIs de TradingAV desde 0

Guía para probar/validar a mano las dos APIs con Postman. Pensada para arrancar
de cero. Todo lo que necesitás ya está en `docs/postman/`.

## Qué es y por qué te sirve

Postman es un cliente para pegarle a las APIs sin escribir código: mandás un
request, ves la respuesta cruda. Te sirve para **validar tus propios datos**
(¿el endpoint de operaciones devuelve lo que espero?, ¿el AuM cierra?), depurar,
y entender qué expone cada API — sin pasar por el frontend.

**Las dos APIs:**
- **Principal** — `api.acaquant.com` (la que consume acaquant-web). ~200 endpoints.
- **Partner** — `data.acaquant.com` (datos de portfolio para el proveedor externo). 5 endpoints.

**El flujo profesional** (el que conviene aprender): FastAPI genera el esquema
**OpenAPI** solo → Postman lo importa → te arma la colección con TODOS los
endpoints. No se escribe nada a mano y queda en sync con el código.

---

## Paso 1 — Generar los specs (ya hecho, re-corré cuando cambie la API)

```bash
python -m scripts.dump_openapi
```
Escribe `docs/postman/openapi_main.json` y `openapi_partner.json`. **Re-corré esto
cada vez que agregues/cambies endpoints** y re-importá en Postman.

## Paso 2 — Importar las colecciones

En Postman: **Import** → arrastrá los dos archivos:
- `docs/postman/openapi_main.json` → colección "TradingAV API"
- `docs/postman/openapi_partner.json` → colección "Acaquant Partner API"

Postman te crea una carpeta por router con cada endpoint y sus parámetros.

## Paso 3 — Importar los environments

**Import** → los dos:
- `docs/postman/acaquant.local.postman_environment.json` → **Local (dev)**
- `docs/postman/acaquant.prod.postman_environment.json` → **Prod**

Un *environment* es un set de variables (`{{base_url_main}}`, `{{api_key}}`, etc.).
Arriba a la derecha elegís cuál usar. Empezá con **Local**.

Cargá los secretos en el environment (lápiz → editar): `api_key` (si la usás),
`partner_user` / `partner_pass` (los del proveedor). Los dejé vacíos a propósito —
**nunca** se commitean secretos.

---

## Paso 4 — Auth de la API principal

La API principal tiene 2 capas:
1. **Bearer `API_KEY`** (header `Authorization: Bearer <api_key>`) — común a todo.
2. **Identidad del usuario** — de quién sos (define qué módulos ves, RBAC).

### Opción A — LOCAL (recomendada para empezar)

Corré la API en tu máquina apuntando al mismo Atlas de prod (datos reales, sin CF):
```bash
uvicorn api.main:app --reload --port 8000
```
En dev (sin `API_KEY` en `.env`) la auth está desactivada, y la identidad se toma
del header `x-acaquant-user-email`. Entonces, en la **colección** (botón ⋯ →
Edit → pestaña):
- **Authorization**: type `Bearer Token`, token `{{api_key}}` (vacío en local = OK).
- **Headers** (pestaña de la colección, "Headers" → se heredan a todos los requests):
  agregá `x-acaquant-user-email` = `{{user_email}}`.
- **Variables** de la colección: poné `baseUrl` = `{{base_url_main}}`.

Con `user_email` = tu mail de admin, ves TODO. Listo: pegale a cualquier endpoint.

### Opción B — PROD (avanzada)

`api.acaquant.com` está detrás de Cloudflare Access. Para pegarle por fuera del
browser necesitás un **CF Access Service Token** (headers `CF-Access-Client-Id`
y `CF-Access-Client-Secret`). Lo creás en el panel de Cloudflare Zero Trust
(Access → Service Auth). Después, en el environment Prod cargás `cf_client_id` /
`cf_client_secret`, y en la colección agregás esos 2 headers + el `api_key` real +
`x-acaquant-user-email`. **Para validar datos, lo más simple es la Opción A**
(local contra el mismo Atlas).

---

## Paso 5 — Auth de la Partner API (login → token)

La Partner usa login usuario/password que devuelve un JWT.

1. Request **`POST {{base_url_partner}}/v1/token`** → pestaña **Body** → **x-www-form-urlencoded** (¡NO JSON!):
   - `username` = `{{partner_user}}`
   - `password` = `{{partner_pass}}`
2. En ese request, pestaña **Scripts → Post-response**, pegá esto para que guarde
   el token solo:
   ```javascript
   const j = pm.response.json();
   if (j.access_token) pm.environment.set("partner_token", j.access_token);
   ```
3. Los demás endpoints (`/v1/fechas`, `/v1/portfolio`): **Authorization** type
   `Bearer Token`, token `{{partner_token}}`. (O ponelo a nivel colección y se
   hereda.)

Flujo: mandás `/v1/token` una vez → el script guarda el JWT → el resto de los
requests lo usan automático. El token vence; si te da 401, re-mandá `/v1/token`.

---

## Paso 6 — Validar datos (ejemplos para tu caso)

Con el environment **Local** activo y la API corriendo:
- `GET {{base_url_main}}/api/health` → `{"status":"ok"}` (chequeo base).
- `GET {{base_url_main}}/api/me` → tu email + role + módulos (confirma la identidad).
- `GET {{base_url_main}}/api/operaciones/ops/fechas` → fechas con operaciones.
- `GET {{base_url_main}}/api/operaciones/ops/agro?desde=2026-01-01&hasta=2026-06-03&agg=MENSUAL`
  → volumen agro + share (validás contra los números que conocés).
- `GET {{base_url_main}}/api/operaciones/ops/serie?moneda=ARS` → la serie de volumen.

Tip: en Postman podés escribir **tests** (pestaña Scripts) que validan solos, ej.:
```javascript
pm.test("status 200", () => pm.response.to.have.status(200));
pm.test("hay fechas", () => pm.expect(pm.response.json().fechas.length).to.be.above(0));
```

---

## Mantenerlo en sync

Cuando cambie la API (endpoints nuevos/modificados):
1. `python -m scripts.dump_openapi`
2. Re-importás los dos `.json` en Postman (sobreescribe la colección).

Los environments NO cambian (solo variables). Los secretos viven solo en tu
Postman, nunca en el repo.
