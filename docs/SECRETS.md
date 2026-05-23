# SECRETS — inventario y rotación

Qué llave abre qué, dónde vive, y cómo cambiarla. Ninguna de estas va al repo:
viven en `/root/TradingAV/.env` (las lee `config.py` vía `load_dotenv()`), en
algunos systemd unit (`Environment=`), y las del frontend en las env vars de
Vercel.

**Regla:** rotar = regenerar en la fuente → actualizar el `.env` (y Vercel si el
frontend la usa) → reiniciar el servicio afectado. Tras tocar `.env`, la API
necesita `systemctl restart api.service`; los crons toman el cambio solos (cada
run es un proceso nuevo).

---

## 🔴 Críticos (mueven plata o dan acceso amplio)

| Secreto | Qué es / dónde se usa | Cómo rotar | Si se filtra |
|---|---|---|---|
| `ROFEX_USER` / `ROFEX_PASSWORD` / `ROFEX_ACCOUNT` | Credenciales del broker (pyRofex). La API y `motor_ordenes` las usan para **enviar y seguir órdenes reales**. | Portal del broker / pyRofex (cambiar password). | Alguien podría **operar tu cuenta**. Máxima prioridad. |
| `MONGO_URI` | Cadena de conexión a Atlas (no está en `config.py`; la lee `core/mongo.py`). Acceso total a todas las DBs. | Atlas → Database Access → editar el password del user → actualizar la URI. | Acceso total a datos de clientes. Rotar + revisar IP whitelist. |
| `API_KEY` | Bearer de la API (`api/deps.py`). El frontend la manda en cada request. | Generar un random nuevo → `.env` del backend **y** env var en Vercel (deben coincidir) → restart API + redeploy front. | Acceso a la API saltando el bearer (pero CF Access sigue adelante). |
| `PARTNER_JWT_SECRET` / `PARTNER_MONGO_URI` | Firma de JWT y DB de `partner_api` (servicio externo, `ACAPortfolio.Cartera`). | Random nuevo / rotar en Atlas → restart `partner_api.service`. | Acceso a la API del proveedor / a su DB. |

## 🟠 Medios (acceso a datos o a servicios pagos)

| Secreto | Qué es | Cómo rotar |
|---|---|---|
| `AUNESA_CLIENT_ID` / `AUNESA_USERNAME` / `AUNESA_PASSWORD` | Credenciales del custodio (Aunesa) — fuente de movimientos/posiciones. | Coordinar con Aunesa. |
| `MCP_JWT_SECRET` | Firma los JWT que emite el server MCP tras CF Access. | Random nuevo → restart API. Invalida tokens MCP vivos (re-login). |
| `MCP_BEARER_TOKEN` | Bearer estático fallback para `/mcp` (curl/dev). | Random nuevo → restart API. |
| `TELEGRAM_BOT_TOKEN` | Bot de alertas (@acaquantbot). | @BotFather → /mybots → Revoke token → actualizar `.env`. |
| `BYMA_CLIENT_ID` / `BYMA_CLIENT_SECRET` | OAuth2 para licitaciones primarias BYMA. | Portal BYMA Developer. |
| `MAE_API_KEY` | MarketData MAE (repos/cauciones wholesale). | Coordinar con MAE. |
| `FINNHUB_API_KEY` | Data de mercado externa. | Dashboard de Finnhub. |
| `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | LLM del asistente (legacy, no en uso hoy). | Consola Anthropic / Google. |

## 🟢 Config sensible (no son secretos, pero cuidá quién los edita)

| Var | Qué es |
|---|---|
| `CF_ACCESS_TEAM` / `CF_ACCESS_AUD` | Identifican el tenant/app de Cloudflare Access para validar el JWT. Si faltan, el JWT no se valida (modo dev). |
| `CF_TRUSTED_SERVICE_TOKENS` | `common_names` de máquinas confiables (ej. el frontend Vercel). |
| `MANAGER_EMAILS` | Emails admin de bootstrap (fallback al RBAC de `Manager.Users`). |
| `ENV` | `prod` activa el fail-closed de auth (EXT-AUTH1). |

## Frontend (env vars en Vercel, no en el `.env` del Droplet)

- `API_URL` → apunta a `https://api.acaquant.com`.
- `API_KEY` → debe coincidir con la del backend.
- `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` → service token con el que el
  SSR de acaquant-web pega al backend tras CF Access (`proxy.ts`).

---

## Procedimiento de rotación (general)

1. **Regenerar** la credencial en su fuente (portal del proveedor, Atlas, BotFather…).
2. **Actualizar** donde viva: `.env` del Droplet (`nano /root/TradingAV/.env`) y/o
   env var en Vercel y/o el systemd unit.
3. **Reiniciar** lo afectado: `systemctl restart api.service` (y `partner_api.service`
   si aplica). Frontend: redeploy en Vercel.
4. **Verificar** que el servicio levantó OK (`systemctl status`, o un request de prueba).

**Cuándo rotar:** ante sospecha de filtración (alguien vio un `.env`, un token en
un log, etc.), cuando se va alguien del equipo con acceso al servidor, y como
higiene periódica para las críticas (broker, Mongo) — al menos 1 vez al año.

## Higiene

- El `.env` **nunca** se commitea (verificar que esté en `.gitignore`).
- No pegar secretos en logs, chats, ni URLs (ej. la URL de `getUpdates` de
  Telegram lleva el token — no compartirla).
- Revisión de accesos de usuarios: `/manager → USUARIOS` (último acceso + badge
  INACTIVO; ver `docs/RUNBOOK.md`).
