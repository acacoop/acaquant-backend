# Interbanking — integración

Estado: **EXPLORACIÓN**. Hay cliente y diags; todavía no hay job, tabla ni vista.
Nada de esto está en producción.

**Verificado el 2026-08-14** (corrido contra prod, no inferido):

- La autenticación funciona con `POST /cas/oidc/oidcAccessToken`, credenciales
  por **Basic header** y `scope=info-financiera`. Son los defaults de `config.py`,
  así que no hace falta setear los overrides en el `.env`.
- `GET /accounts` con `account-type=CC` devuelve **26 cuentas**. Bancos vistos:
  007, 017, 034, 191, 198, 254, 299, 322, 431.
- Falta correr el listado de `CA` y el cruce contra `operaciones.tesoreria_cuentas`.

## Qué es y para qué sirve

Interbanking es la plataforma por la que ACA opera con sus bancos. Expone 5 APIs
REST de **solo lectura** (todas GET — no hay ningún endpoint que origine pagos,
así que la integración no puede mover plata ni por error).

| API | Endpoint | Qué trae |
|---|---|---|
| Cuentas | `/accounts`, `/accounts/{n}` | CBU, número, banco (código BCRA + nombre), tipo CC/CA, moneda, denominación |
| Saldos | `/accounts/{n}/balances` | Contable, operativo actual, **operativo inicial**, proyectados 24/48hs + histórico diario |
| Extractos | `/accounts/{n}/statements` | Extracto oficial por día: apertura, cierre, totales de débitos/créditos + detalle |
| Movimientos | `/{v1\|v2}/accounts/{n}/movements/{tipo}` | Movimientos del día / anteriores / **diferidos** (fecha futura) / zughus |
| Transferencias | `/transfers/details`, `/transfers/vouchers` | Transferencias en todos sus estados + comprobantes con bloque AFIP (`vep_number`) |

**Por qué importa** — hoy la vista de Tesorería tiene tres agujeros que estas
APIs tapan:

1. **El saldo inicial se tipea a mano** todos los días en `tesoreria_saldos`, y
   si el back office no lo carga, vale 0. Saldos lo da: `initial_operating_balance`.
2. **El universo de cuentas se descubre mirando movimientos** de Aunesa, porque
   Aunesa no tiene endpoint de cuentas. Interbanking sí lo tiene, y encima trae
   el CBU y el número de cuenta, que hoy son carga manual.
3. **Nadie concilia** el saldo final que calcula la grilla BANCOS contra lo que
   dice el banco. Extractos permite ese chequeo.

Los movimientos **diferidos** (con fecha futura) son información que hoy no
existe en ninguna fuente del sistema.

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
- `account-type` **filtra**: el universo completo de cuentas son dos llamadas
  (CC y CA). Eso hace `todas_las_cuentas()`.

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

## Orden propuesto, de menor a mayor riesgo

1. **Cuentas** — read-only puro, no toca ningún cálculo. Valida la auth.
2. **Saldos** — automatizar el saldo inicial, primero como *sugerencia* al lado
   del campo manual, sin pisarlo.
3. **Extractos** — conciliación diaria, como un chequeo de SALUD.
4. **Transferencias / Movimientos** — enriquecimiento, con la base ya andando.

## Changelog

- **2026-08-14** — Alta de la aplicación en el portal. Colección de Postman
  (`docs/postman/`), `core/interbanking.py`, `scripts/diag_interbanking_auth.py`,
  `scripts/diag_interbanking.py` y `scripts/diag_interbanking_raw.py`. Detectado
  que el `tokenUrl` de los YAML del proveedor no es el endpoint real. **Auth
  resuelta y 26 cuentas leídas** contra producción.
