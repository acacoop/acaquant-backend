# Partner API — API de datos de portfolio para proveedor externo

API **separada** de `api.acaquant.com` que expone, a un proveedor externo,
el AuM de unas pocas cuentas puntuales. Servicio propio, base de datos
aislada, auth propia.

## Arquitectura

```
  jobs.aum ──► Valuaciones.AuM ──► jobs.partner_export (cron 23:45 UTC)
                                         │ SOLO config.PARTNER_EXPORT_CUENTAS
                                         ▼
                                 Partner.PortfolioExport
                                         │  (Mongo user read-only en `Partner`)
  Proveedor ──HTTPS──► data.acaquant.com ─┴─► partner_api  (uvicorn :8100)
              Bearer JWT                       proceso systemd aparte
```

- **`jobs/partner_export.py`** — cron diario (23:45 UTC, post-AuM). Vuelca a
  `Partner.PortfolioExport` SOLO las cuentas de `config.PARTNER_EXPORT_CUENTAS`
  y SOLO los campos `{fecha, id_cuenta, cuenta, unidad, cantidad, precio,
  valuacion}`. Idempotente por fecha, mantiene histórico.
- **`partner_api/`** — servicio FastAPI aparte (puerto 8100). Lee
  `Partner.PortfolioExport`. No comparte proceso ni conexión Mongo con la mesa.

## Seguridad — capas

1. **Aislamiento de datos (lo más fuerte).** El servicio se conecta a Mongo
   con un usuario **read-only scopeado a la base `Partner`**. No puede leer
   `Valuaciones`/`Manager`/`CashFlow` ni escribir nada. Y `Partner.PortfolioExport`
   sólo contiene las cuentas habilitadas — no existe forma de pedir otra.
2. **Auth.** Usuario/password → JWT de vida corta (60 min). Sin token válido
   no se devuelve nada. Passwords hasheados con PBKDF2 (`Partner.ApiUsers`) —
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

1. **Atlas** — crear un DB user nuevo, ej. `partner_ro`, con rol **`read`
   sobre la base `Partner`** únicamente. Copiar su connection string.
2. **`.env`** del Droplet — agregar:
   ```
   PARTNER_MONGO_URI=mongodb+srv://partner_ro:...@.../Partner
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
