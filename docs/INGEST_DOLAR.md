# Ingesta del dólar oficial — cómo cerrar el `0.0.0.0/0` de Atlas

El dólar oficial mayorista lo provee MAE, que **solo acepta la IP de la oficina**
(rechaza la del Droplet). Antes, el script `mae_forex.py` poleaba MAE y escribía
**directo a Atlas** desde una IP dinámica → obligaba a tener Atlas abierto a todo
internet (`0.0.0.0/0`), el mayor riesgo de seguridad del sistema.

**Solución:** el script manda el dato por HTTPS a la API; el Droplet (IP fija,
whitelisteada) lo escribe en `Valuaciones.DolarOficialLive`. La oficina deja de
tocar Mongo → Atlas se cierra a la IP del Droplet.

```
notebook (manual, IP cualquiera)  ──POST + tokens──▶  api.acaquant.com  ──▶  Atlas
   pollea MAE → enviar_a_api(docs)                   (Droplet, IP fija)   DolarOficialLive
```

Piezas (ya en el repo): `api/routers/ingest.py` (endpoint), `core.dolar_oficial.upsert_oficial`
(escritura), `scripts/mae_forex_client.py` (cliente), `scripts/smoke_ingest_dolar.py` (prueba).

---

## Playbook (en este orden — Atlas se cierra AL FINAL)

### 1. Backend (Droplet)
```
git pull
python -c "import secrets; print(secrets.token_urlsafe(32))"   # token nuevo
```
Poné `DOLAR_INGEST_TOKEN=<ese valor>` en `/root/TradingAV/.env` → `systemctl restart api.service`.

### 2. Cloudflare Access (service token para la oficina)
- CF Zero Trust → **Access → Service Auth → Create Service Token** (`dolar-oficial`).
  Guardá **Client ID** y **Client Secret** (el secret se muestra una sola vez).
- App de Access que protege `api.acaquant.com` → **policies** → agregá una con
  **Action = `Service Auth`** (NO "Allow") e Include = Service Token `dolar-oficial`.
- No hace falta tocar `CF_TRUSTED_SERVICE_TOKENS`: el endpoint valida su propio
  `X-Ingest-Token`, no el `common_name`.

### 3. Validar el camino (sin tocar nada todavía)
Desde el notebook (con `pip install requests`), con las 4 env vars seteadas:
```
ACAQUANT_API_URL=https://api.acaquant.com DOLAR_INGEST_TOKEN=... \
CF_ACCESS_CLIENT_ID=... CF_ACCESS_CLIENT_SECRET=... \
python -m scripts.smoke_ingest_dolar
```
Manda un doc de PRUEBA (`SMOKE_TEST`, no contamina el dólar real). Debe dar `✅ 200`.
Si no: HTML de login → policy CF mal (revisá Service Auth); 401 → token no coincide;
503 → falta el token en el `.env` del Droplet (o no reiniciaste).

### 4. Migrar el cliente real
En tu `mae_forex.py`, donde hoy escribís a Mongo, llamá a `enviar_a_api(docs)` de
`scripts/mae_forex_client.py` (o pegá tu polling de MAE en su `obtener_instrumentos_mae`).
Dejá el polling tal cual; solo cambia a dónde va el dato. Corré y verificá en la web
que el dólar oficial se actualiza (o que `DolarOficialLive.updated_at` esté fresco).

### 5. 🎯 Cerrar Atlas (recién ahora)
Con el dólar entrando por la API: Atlas → **Network Access** → quitá `0.0.0.0/0`,
dejá solo la **IP del Droplet** (+ tu IP para administrar si querés). Agujero cerrado.

---

## Seguridad

- El feed **nunca se corta**: hasta el paso 5 escribe como siempre. Si algo del
  3-4 falla, **no cierres Atlas** y seguís como estás.
- `DOLAR_INGEST_TOKEN` está acotado: si se filtra, solo permite escribir
  `DolarOficialLive` — no da acceso a Mongo. Rotarlo: ver `docs/SECRETS.md`.
- Doble capa: CF Access (service token, a nivel red) + `X-Ingest-Token` (a nivel app).
