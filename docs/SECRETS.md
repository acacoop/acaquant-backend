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
| `POSTGRES_URI` | Cadena de conexión a Postgres/Supabase (la lee `core/postgres.py`). Acceso total a las DBs SQL. | Supabase → Database → rotar el password del rol → actualizar la URI. | Acceso total a datos de clientes. Rotar + revisar reglas de red. |
| `API_KEY` | Bearer de la API (`api/deps.py`). El frontend la manda en cada request. | Generar un random nuevo → `.env` del backend **y** env var en Vercel (deben coincidir) → restart API + redeploy front. | Acceso a la API saltando el bearer (pero CF Access sigue adelante). |

> **Obsoletos (ya NO se leen — eliminables del `.env`):** `MONGO_URI`, `ATLAS_*`.
> Quedaron del stack Mongo/proveedor,
> decomisado 2026-06-29; el código
> ya no los usa. El secreto vivo de base de datos es `POSTGRES_URI`.

## 🟠 Medios (acceso a datos o a servicios pagos)

| Secreto | Qué es | Cómo rotar |
|---|---|---|
| `AUNESA_CLIENT_ID` / `AUNESA_USERNAME` / `AUNESA_PASSWORD` | Credenciales del custodio (Aunesa) — fuente de movimientos/posiciones. | Coordinar con Aunesa. |
| `BYMA_CLIENT_ID` / `BYMA_CLIENT_SECRET` | OAuth2 para licitaciones primarias BYMA. | Portal BYMA Developer. |
| `MAE_API_KEY` | MarketData MAE (repos/cauciones wholesale). | Coordinar con MAE. |
| `DOLAR_INGEST_TOKEN` | Token de `POST /api/ingest/dolar-oficial` (la PC de oficina lo manda en `X-Ingest-Token`). Va en el `.env` del Droplet **y** en la oficina (deben coincidir). Si se filtra: solo permite escribir el dólar oficial live, no da acceso a la DB. | Random nuevo → `.env` Droplet + oficina → restart API. |
| `FINNHUB_API_KEY` | Data de mercado externa. | Dashboard de Finnhub. |
| `RESEARCH_IMAP_USER` / `RESEARCH_IMAP_PASSWORD` / `RESEARCH_MAIL_FROM` | Casilla que recibe el research diario + app password + remitente(s) — los lee `jobs/research_mail.py` (IMAP readonly, QuantAI P6). OJO: la app password da acceso de LECTURA a toda la casilla — usar una app password dedicada, jamás la contraseña real. | Gmail: Cuenta → Seguridad → Contraseñas de aplicaciones → revocar y generar otra → `.env` (el cron la toma solo). |
| `FRED_API_KEY` | API key de FRED (Federal Reserve de St. Louis) — la lee `core/fred_api.py` (tab DATOS INTERNACIONALES de Research, `jobs/fred_research.py`). Gratis, solo lectura de data pública sin cargo: si se filtra, el daño máximo es que un tercero use tu cuota. Sin ella, la tab FRED queda sin datos (todo lo demás sigue igual). | fredaccount.stlouisfed.org/apikeys → regenerar → `.env` (el cron la toma solo; restart API para que sirva la tab). |
| `DEEPSEEK_API_KEY` | Proveedor LLM **default** del gateway (`core/llm.py`). ⚠️ **HOY NINGUNA TAREA LO USA** — el sistema no tiene features de IA desde el 2026-08-28; la clave queda porque el gateway se conservó. Cuenta prepaga, saldo chico: si se filtra, el daño máximo es quemar el saldo. ⚠ Este proveedor SÍ puede entrenar con lo que se le manda → jamás datos del negocio. | platform.deepseek.com → API Keys → regenerar → `.env` → restart API. **Si no vas a volver a usar IA, sacala del `.env`**: una key viva que nadie usa es saldo expuesto sin contrapartida. |
| `OPENAI_API_KEY` | Proveedor LLM para tareas marcadas `datos:"negocio"` — se eligió por su compromiso de NO entrenar con datos de API + borrado a 30 días (decisión user 2026-07-21). El gateway es **fail-closed**: sin esta key, una tarea de negocio NO cae a DeepSeek, se niega. ⚠️ **HOY NINGUNA TAREA LO USA** (ver arriba). | platform.openai.com → API keys → regenerar → `.env` → restart API. Data controls: sharing en **Disabled**, API call logging **Disabled**, audit logging **Enabled**. |
| `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | LLM del asistente (legacy, no en uso hoy). | Consola Anthropic / Google. |

## 🟢 Config sensible (no son secretos, pero cuidá quién los edita)

| Var | Qué es |
|---|---|
| `CF_ACCESS_TEAM` / `CF_ACCESS_AUD` | Identifican el tenant/app de Cloudflare Access para validar el JWT. Si faltan, el JWT no se valida (modo dev). |
| `CF_TRUSTED_SERVICE_TOKENS` | `common_names` de máquinas confiables (ej. el frontend Vercel). |
| `MANAGER_EMAILS` | Emails admin de bootstrap (fallback al RBAC de `manager.manager_users`). |
| `ENV` | `prod` activa el fail-closed de auth (EXT-AUTH1). |

## Frontend (env vars en Vercel, no en el `.env` del Droplet)

- `API_URL` → apunta a `https://api.acaquant.com`.
- `API_KEY` → debe coincidir con la del backend.
- `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` → service token con el que el
  SSR de acaquant-web pega al backend tras CF Access (`proxy.ts`).

---

## Procedimiento de rotación (general)

1. **Regenerar** la credencial en su fuente (portal del proveedor, Supabase, BotFather…).
2. **Actualizar** donde viva: `.env` del Droplet (`nano /root/TradingAV/.env`) y/o
   env var en Vercel y/o el systemd unit.
3. **Reiniciar** lo afectado: `systemctl restart api.service`. Frontend: redeploy en Vercel.
4. **Verificar** que el servicio levantó OK (`systemctl status`, o un request de prueba).

**Cuándo rotar:** ante sospecha de filtración (alguien vio un `.env`, un token en
un log, etc.), cuando se va alguien del equipo con acceso al servidor, y como
higiene periódica para las críticas (broker, base de datos) — al menos 1 vez al año.

## Higiene

- El `.env` **nunca** se commitea (verificar que esté en `.gitignore`).
- No pegar secretos en logs, chats, ni URLs.
- Revisión de accesos de usuarios: `/manager → USUARIOS` (último acceso + badge
  INACTIVO; ver `docs/RUNBOOK.md`).
