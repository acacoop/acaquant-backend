# Partner API — API de datos de portfolio para proveedor externo

API **separada** de `api.acaquant.com` que expone, a un proveedor externo,
el AuM de unas pocas cuentas puntuales. Servicio propio, base de datos
aislada, auth propia.

## Arquitectura

```
  jobs.aum ──► Valuaciones.AuM ──► jobs.partner_export (cron 23:45 UTC)
                                         │ SOLO config.PARTNER_EXPORT_CUENTAS
                                         ▼
                                 ACAPortfolio.Cartera
                                         │  (Mongo user read-only en `ACAPortfolio`)
  Proveedor ──HTTPS──► data.acaquant.com ─┴─► partner_api  (uvicorn :8100)
              Bearer JWT                       proceso systemd aparte
```

- **`jobs/partner_export.py`** — cron diario (23:45 UTC, post-AuM). Vuelca a
  `ACAPortfolio.Cartera` SOLO las cuentas de `config.PARTNER_EXPORT_CUENTAS`
  y SOLO los campos `{fecha, id_cuenta, cuenta, unidad, cantidad, precio,
  valuacion}`. Idempotente por fecha, mantiene histórico.
- **`partner_api/`** — servicio FastAPI aparte (puerto 8100). Lee
  `ACAPortfolio.Cartera`. No comparte proceso ni conexión Mongo con la mesa.

## Migración Mongo → SQL (para apagar Mongo)

El partner_api fue migrado al mismo patrón **dual-run** del resto del sistema:
las mismas dos colecciones de `ACAPortfolio` viven ahora también en Postgres
(Supabase), en un schema PROPIO `partner` (ver `sql/schema.sql` §PARTNER y
`docs/SQL.md`).

- **Tablas SQL**: `partner.cartera` (espejo de `Cartera`, columnas materializadas
  — shape fijo y conocido, sin jsonb) y `partner.api_users` (espejo de
  `ApiUsers`, con el `password_hash` tal cual). `fecha` se guarda como `date`,
  `exported_at`/`created_at` como `timestamptz` (en Mongo son datetime aware UTC).
- **Conexión propia**: `partner_api/pg.py` (NO importa `core.postgres`; usa la env
  `POSTGRES_URI` y SIEMPRE califica `partner.<tabla>` — la API de la mesa nunca
  resuelve sin querer una tabla de un tercero). Auto-crea schema+tablas en el
  primer uso (`ensure_schema()`).
- **Lectura** (`partner_api/store.py`) — selector por flag de entorno:
  - `PARTNER_SQL` ausente / `0` (**DEFAULT**) → lee **Mongo** (path original
    INTACTO). El proveedor no nota ningún cambio.
  - `PARTNER_SQL=1` → lee **Postgres**. Devuelve el MISMO shape (mismo orden,
    mismos campos) en los dos backends. Cubre REST (`/v1/*`) Y OData (`/odata/*`).
  - Rollback = sacar la env + `systemctl restart partner_api.service`.
- **Escritura** (gateada por `PARTNER_SQL_WRITE=1`, default apagada → dual-write
  best-effort, si PG falla NO rompe el path Mongo):
  - Cartera: `jobs/partner_export.py` escribe Mongo **y** `partner.cartera` (mismo
    delete+insert idempotente por `(id_cuenta, fecha)`).
  - Usuarios: `scripts/partner_user.py` (crear/reset/habilitar/deshabilitar)
    upsertea también `partner.api_users`.
- **Baseline (seed inicial)**: `python -m scripts.partner_sql_baseline` — vuelca
  el contenido actual de `ACAPortfolio` a las tablas SQL (idempotente, `--dry-run`
  para contar). NO va en `jobs/sync_postgres.py` (el partner es un dominio
  separado). Después, el dual-write mantiene SQL al día.

**Orden de cutover** (sin downtime para el proveedor):
1. Correr el schema (`sql/schema.sql`) o dejar que `ensure_schema()` cree las tablas.
2. `PARTNER_SQL_WRITE=1` en el `.env` + restart → dual-write activo (Mongo sigue
   siendo la fuente de lectura).
3. `python -m scripts.partner_sql_baseline` → seed del histórico.
4. Verificar paridad (comparar `/v1/portfolio` y `/v1/fechas` con y sin
   `PARTNER_SQL=1` apuntando a las mismas fechas).
5. `PARTNER_SQL=1` + restart → la lectura pasa a SQL. Rollback inmediato sacando
   la env.
6. Cuando se decida apagar Mongo: quitar `PARTNER_MONGO_URI` deja de aplicar (el
   servicio ya no lo usa con `PARTNER_SQL=1`); `partner_api/db.py` queda como
   código muerto a borrar en un commit posterior.

## Seguridad — capas

1. **Aislamiento de datos (lo más fuerte).** El servicio se conecta a Mongo
   con un usuario **read-only scopeado a la base `ACAPortfolio`**. No puede leer
   `Valuaciones`/`Manager`/`CashFlow` ni escribir nada. Y `ACAPortfolio.Cartera`
   sólo contiene las cuentas habilitadas — no existe forma de pedir otra.
2. **Auth.** Usuario/password → JWT de vida corta (60 min). Sin token válido
   no se devuelve nada. Passwords hasheados con PBKDF2 (`ACAPortfolio.ApiUsers`) —
   nunca en texto plano.
3. **Transporte.** Sólo HTTPS. Detrás del proxy de Cloudflare (WAF + DDoS).
4. **Rate limiting.** `/v1/token` 10/min (anti fuerza bruta), datos 60/h.
5. **Auditoría.** Cada request se loguea (IP, usuario, endpoint, status).
6. **Aislamiento de proceso.** Servicio systemd aparte — si se cae o lo
   atacan, `api.acaquant.com` no se entera.
7. **Sin OpenAPI público.** `docs_url`/`openapi_url` deshabilitados.

**Peor caso** (se filtran las credenciales del proveedor): el atacante puede
leer las posiciones de las cuentas habilitadas; **no** puede ver otras
cuentas, modificar nada, ni alcanzar el resto del sistema. Se detecta por el
log de auditoría y se corta al instante deshabilitando el usuario.

**Pendiente recomendado**: pedirle al proveedor sus IPs fijas y sumar un
allowlist (a nivel Cloudflare o nginx) — es la defensa más fuerte.

## Deploy (Droplet)

1. **Atlas** — crear un DB user nuevo, `aca_1`, con rol **`read`
   sobre la base `ACAPortfolio`** únicamente. Copiar su connection string.
2. **`.env`** del Droplet — agregar:
   ```
   PARTNER_MONGO_URI=mongodb+srv://aca_1:...@.../ACAPortfolio
   PARTNER_JWT_SECRET=<string random largo>
   # opcional: PARTNER_TOKEN_TTL_MIN=60
   ```
   Generar el secret: `python -c "import secrets; print(secrets.token_urlsafe(48))"`
3. `git pull` en `/root/TradingAV`.
4. **systemd**:
   ```
   cp deploy/systemd/partner_api.service /etc/systemd/system/
   systemctl daemon-reload
   systemctl enable --now partner_api.service
   ```
5. **nginx** — server block para `data.acaquant.com` → `proxy_pass http://127.0.0.1:8100;`
   con su certificado TLS.
6. **Cloudflare** — registro DNS `data` apuntando al Droplet, **proxied**
   (nube naranja). **NO** crear una app de Cloudflare Access para este
   subdominio (el proveedor no tiene el SSO de la empresa).
7. **Cron** — ya está en `deploy/crontab.txt` (línea 23:45 UTC). Aplicar el
   crontab si hace falta: `crontab /root/TradingAV/deploy/crontab.txt`.
8. **Primera carga de datos**: `python -m jobs.partner_export`.

## Crear / gestionar usuarios del proveedor

```
python -m scripts.partner_user crear        <username>   # crea + imprime password
python -m scripts.partner_user reset        <username>   # nuevo password
python -m scripts.partner_user deshabilitar <username>   # corta el acceso ya
python -m scripts.partner_user habilitar    <username>
python -m scripts.partner_user listar
```

`crear`/`reset` imprimen el password **una sola vez** — copialo y entregáselo
al proveedor por un canal seguro.

## Instrucciones para el proveedor

> **Base URL:** `https://data.acaquant.com`
>
> **1. Obtener un token** (vence en 60 min) — `POST /v1/token`, form-encoded:
> ```
> curl -X POST https://data.acaquant.com/v1/token \
>   -d "username=<usuario>" -d "password=<password>"
> ```
> Respuesta: `{"access_token": "...", "token_type": "bearer", "expires_in": 3600}`
>
> **2. Pedir el portfolio** — `GET /v1/portfolio` con el token:
> ```
> curl https://data.acaquant.com/v1/portfolio \
>   -H "Authorization: Bearer <access_token>"
> ```
> Parámetros opcionales: `?fecha=YYYY-MM-DD` (default: la más reciente),
> `?id_cuenta=101`.
>
> **Otros endpoints:** `GET /v1/fechas` (fechas disponibles), `GET /health`.
>
> Respuesta de `/v1/portfolio`:
> ```json
> {
>   "fecha": "2026-05-16",
>   "posiciones": [
>     {"id_cuenta": "101", "cuenta": "[101] ...", "unidad": "...",
>      "cantidad": 100.0, "precio": 98.5, "valuacion": 9850.0}
>   ],
>   "n": 1
> }
> ```

## Acceso OData v2 (SAP Datasphere y herramientas SAP)

Para herramientas que NO consumen REST nativo (ej. **SAP Datasphere**, que se
conecta vía OData), el servicio expone los **mismos datos** como un **servicio
OData v2 estándar**, en paralelo a la REST (no la reemplaza). Implementado en
`partner_api/odata.py`.

- **Service URL:** `https://data.acaquant.com/odata/`
- **Tipo de conexión en Datasphere:** *Generic OData* (OData **V2**).
- **Auth:** **HTTP Basic** con el **mismo usuario/password** del proveedor
  (los de `ACAPortfolio.ApiUsers`) — no usa el flow del JWT.
- **Entidad:** `Portfolio` (una fila por posición). Propiedades: `ID` (key
  sintética `fecha|id_cuenta|unidad`), `fecha`, `id_cuenta`, `cuenta`, `unidad`,
  `cantidad`, `precio`, `valuacion`.

Endpoints:

| Endpoint | Auth | Qué es |
|---|---|---|
| `GET /odata/` | abierto | service document (lista las entidades) |
| `GET /odata/$metadata` | abierto | esquema EDMX (Datasphere lo descubre solo) |
| `GET /odata/Portfolio` | Basic | los datos (posiciones) |
| `GET /odata/Portfolio/$count` | Basic | conteo de filas |

Query options soportadas en `/odata/Portfolio`:
`$filter` (igualdades: `fecha eq '2026-05-16'`, `id_cuenta eq '101'`, unidas por
`and`), `$top`, `$skip`, `$select`, `$inlinecount=allpages` (devuelve `__count`).

> **Instrucciones para el proveedor (Datasphere):**
> 1. Crear una conexión **Generic OData**, versión **V2**.
> 2. URL del servicio: `https://data.acaquant.com/odata/`
> 3. Autenticación: **Basic**, con el usuario/password ya entregados.
> 4. Importar la entidad **`Portfolio`**.
> 5. Ejemplo — posiciones de una fecha:
>    `https://data.acaquant.com/odata/Portfolio?$filter=fecha eq '2026-05-16'`

**Nota técnica:** es OData **v2** (máxima compatibilidad SAP). Los números van
tipados `Edm.Double` → salen como número en el JSON. Si la herramienta del
proveedor exigiera OData **v4**, el cambio es chico (las funciones de formato
están aisladas en `odata.py`) — pedir confirmación de la versión.
