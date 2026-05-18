# WIP — Partner API: qué falta (continuamos acá)

> Doc de handoff. El **código está 100% hecho y pusheado** (commits
> `85bcc82` + `8b7d7ad`). Falta SOLO la infra en el Droplet/Atlas/Cloudflare.
> Doc canónico de la feature: `docs/PARTNER_API.md`.

**Dónde retomamos:** Paso 1 (Atlas). Hacer de a un paso, con calma.

---

## Paso 1 — Atlas: usuario Mongo read-only

Crear un usuario de base de datos **de solo lectura, limitado a la base `ACAPortfolio`**.

- Atlas → **Database Access** → **Add New Database User**.
- Authentication: **Password**. Username: `aca_1`. Generar password.
- **Database User Privileges** → **Specific Privileges**:
  - Role: `read` · Database: `ACAPortfolio` (Collection vacío = toda la base).
- Add User.
- Guardar el connection string (Atlas → Connect → Drivers), reemplazando
  usuario/password por los de `aca_1`.

⚠️ Chequear también que el usuario **principal** (`MONGO_URI`) tenga
**escritura** sobre la base nueva `ACAPortfolio` — la necesitan `jobs.partner_export`
y `scripts.partner_user`. Si es `readWriteAnyDatabase`/`atlasAdmin`, ya está.

## Paso 2 — `.env` del Droplet

Agregar dos líneas:
```
PARTNER_MONGO_URI=<connection string del aca_1, con /ACAPortfolio al final>
PARTNER_JWT_SECRET=<pegar el resultado del comando de abajo>
```
Generar el secret:
```
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Paso 3 — systemd (arrancar el servicio)

```
cd /root/TradingAV && git pull
cp deploy/systemd/partner_api.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now partner_api.service
systemctl status partner_api.service
```
Tiene que quedar `active (running)`. La API queda escuchando en
`127.0.0.1:8100` (todavía no accesible desde afuera — falta el paso 4).

## Paso 4 — Cloudflare + nginx (exponer data.acaquant.com)

- Cloudflare → DNS → registro **A**, nombre `data`, valor = IP del Droplet,
  **Proxied** (nube naranja). **NO** crear app de Cloudflare Access.
- nginx → server block para `data.acaquant.com` con
  `proxy_pass http://127.0.0.1:8100;` + certificado TLS.
- (Este paso tiene más detalle — lo vemos juntos en vivo.)

## Paso 5 — usuario del proveedor + primera carga

```
python -m scripts.partner_user crear <username>   # imprime el password 1 vez
python -m jobs.partner_export                      # carga ACAPortfolio.Cartera
crontab /root/TradingAV/deploy/crontab.txt         # aplica el cron 23:45 UTC
```

## Probar que anda (al final)

```
curl -X POST https://data.acaquant.com/v1/token -d "username=<u>" -d "password=<p>"
curl https://data.acaquant.com/v1/portfolio -H "Authorization: Bearer <token>"
```

## Recordatorios

- Cuentas habilitadas: `101` y `175` (la 3ª se agrega a
  `config.PARTNER_EXPORT_CUENTAS` cuando esté).
- Las instrucciones para entregar al proveedor están en `docs/PARTNER_API.md`.
