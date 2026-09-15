# SECURITY — postura de seguridad y credenciales

> **Un doc.** Absorbió a `SECRETS.md` el 2026-08-31. El inventario de credenciales
> y la postura que las protege son la misma pregunta: quién puede leer qué. Con
> dos archivos, agregar un secreto no obligaba a revisar el gate.


---

# PARTE A — Postura, gates y auditoría

Doc consolidado de cómo se protege `api.acaquant.com`. La seguridad está en
**capas**: cada request pasa por todas las que apliquen. Código fuente de
verdad: `api/auth.py`, `api/deps.py`, `api/main.py`, `api/ratelimit.py`,
`core/roles.py`.

### Las capas (en orden, de afuera hacia adentro)

```
Request
  │
  1. Cloudflare Access ──── ¿el email/máquina puede entrar al sitio?
  │
  2. API_KEY (Bearer) ───── ¿viene del frontend acaquant-web autorizado?
  │
  3. JWT de CF Access ───── validación criptográfica de la identidad
  │
  4. RBAC require_module ── ¿el role de ese email tiene este módulo?
  │
  5. Rate limit (slowapi) ─ ¿no se pasó de cuota?
  │
  ▼  handler
```

### 1. Cloudflare Access — quién entra

CF Access protege los hostnames (`trading.acaquant.com`, `api.acaquant.com`).
Es el IdP: login por OTP/email.

⚠️ **PENDIENTE — la app `acaquant-mcp-bypass` sigue en el panel de Access.**
(BYPASS + Everyone) exime 5 paths sin login — `/mcp`, `/oauth/token`,
`/oauth/register`, `/.well-known/oauth-*` — para un MCP server que **se borró
del repo el 2026-08-28**. Hoy apuntan a 404s, así que no exponen nada, pero
ocupan **5/5 destinations**, la cuota entera, y volverían a estar vivos el día
que algo se monte en esos paths. Sacarla es una acción en Cloudflare, no en
este repo: por eso queda escrito acá y no se borra hasta hacerlo.

### 2. API_KEY — el frontend autorizado

`api/deps.py::verify_api_key` exige `Authorization: Bearer <API_KEY>`. Es el
secreto compartido entre `acaquant-web` (server-side) y la API. Se aplica
como dependency `_PUBLIC` a casi todos los routers en `api/main.py`.

- **`/api/health`** y **`/api/me`** NO llevan el gate (health es trivial;
  `/api/me` devuelve la identidad propia del que llama).

⚠️ **Fail-open dev**: si `API_KEY` no está en `.env`, `verify_api_key` deja
pasar todo. En prod (Droplet) `API_KEY` **debe** estar seteada.

### 3. JWT de Cloudflare Access — identidad real

`api/auth.py` valida criptográficamente el JWT que emite CF Zero Trust
(JWKS de `{CF_ACCESS_TEAM}.cloudflareaccess.com`). Reemplaza la lectura
naïve del header `cf-access-authenticated-user-email` (spoofable).

Hay **dos tipos de JWT**:
1. **User JWT** (login directo) — trae `email`. Ese es el usuario.
2. **Service token JWT** (acaquant-web SSR → API) — trae `common_name`, NO
   `email`. CF Access **estripa** `cf-access-authenticated-user-email` en
   este caso, así que el frontend propaga el email real en un header custom
   que CF no controla: **`x-acaquant-user-email`**.

⚠️ **Fail-open dev**: si `CF_ACCESS_TEAM` / `CF_ACCESS_AUD` no están
configurados, `auth.py` loggea warning y cae al header directo (spoofable).
En prod **deben** estar seteados — ahí un JWT con firma/audience inválida
da 401.

### 4. RBAC — qué ve cada role

CF Access dice quién entra; `core/roles.py` dice qué ve. `require_module(m)`
es la dependency que chequea que el role del email (de `manager.role_matrix`)
tenga el módulo. Aplicado por router en `api/main.py`:

| Grupo | Routers | Roles |
|---|---|---|
| `_PUBLIC` | analitica, cotizaciones, news, market, titulos, scanner, derivados_agro | todos |
| `_PORTFOLIOS` | carteras, valuaciones | admin, trader |
| `_OPERAR` | ordenes, operativa, risk | admin, trader, (sales) |
| `_OPERACIONES` | operaciones, cuentas | admin, trader |
| `_ASISTENTE` | chat | admin, trader |
| `_MANAGER` | manager, manager_resources | admin |

Detalle del modelo RBAC: `api/CLAUDE.md`.

### 5. Rate limiting

`api/ratelimit.py` — slowapi, `SlowAPIMiddleware`. La key es el **email del
usuario** sacado del JWT firmado por CF (`cf-access-jwt-assertion`, no
spoofable); los anónimos comparten **un solo bucket** (`anon`) para que un
atacante no-autenticado no pueda quemar cuota por volumen. Límites por
endpoint vía `@limiter.limit(...)` (ej. `/manager/jobs/run`). *(`/api/chat` era el
otro ejemplo: el asistente legacy se borró el 2026-06-03.)*

### Secretos / env vars

Viven en `.env` (local) y systemd unit files (Droplet). Nunca en el repo.

| Var | Qué protege |
|---|---|
| `API_KEY` | gate Bearer del frontend ↔ API |
| `CF_ACCESS_TEAM` / `CF_ACCESS_AUD` | validación del JWT de CF Access |
| `CF_TRUSTED_SERVICE_TOKENS` | service tokens aceptados (acaquant-web SSR) |
| `POSTGRES_URI` | credenciales de Postgres/Supabase |

Rotación de `API_KEY`: manual — generar nueva, actualizar `.env` del Droplet
+ las env vars de Vercel (acaquant-web), redeploy de ambos.

### Checklist al tocar la API

- Endpoint nuevo → ¿qué grupo de dependency (`_PUBLIC` / `_PORTFOLIOS` / …)?
  Default: el más restrictivo que tenga sentido.
- ¿Expone datos de cuentas/posiciones? → nunca `_PUBLIC`.
- ¿Acción mutante o cara? → `@limiter.limit(...)`.
- ¿Lectura ADMIN? → `require_admin`, no `require_module`. Si el par de
  escritura es admin-only, la lectura casi siempre también (fue el bug de
  `/api/ia/observabilidad`).
- ¿Escritura en un módulo que el invitado VE (mercado/research/ia)? →
  `require_no_invitado` además del gate de módulo.
- En prod `API_KEY`, `CF_ACCESS_TEAM` y `CF_ACCESS_AUD` **tienen que** estar
  seteados — sin ellos la auth es fail-open.
- Antes de pushear router/service: REGLA #1 (ver `api/CLAUDE.md`).
- Revisión de cambios con impacto en auth/datos: `/seguridad-acaquant`.

### Verificar la superficie HTTP

El gate real de un endpoint se compone de tres lugares (montaje en
`api/main.py` + `dependencies=` del sub-router + decorador). Leerlo a ojo no
alcanza — hay que mirar el árbol de rutas ya resuelto:

```bash
python -m scripts.audit_superficie_http          # resumen por categoría
python -m scripts.audit_superficie_http --todo   # inventario completo (361 rutas)
```

El enforcement automático está en `tests/unit/test_rbac_superficie.py` (corre
en CI): recorre `app.routes` y falla si aparece una ruta `/api/*` sin bearer,
una escritura sin gate de módulo, una ruta de `manager` sin gatear, o si al
panel de IA le sacan el `require_admin`.

### Auditoría 2026-08-03 — backlog pendiente

Auditoría de código sobre las 361 rutas (6 dimensiones, cada hallazgo
verificado de forma adversarial). **No hubo pentest contra prod** — todo lo de
abajo sale de leer el código, y lo que depende de datos reales está marcado
como no verificado (REGLA #2).

Ya corregido en el commit de la auditoría:

- **`GET /api/ia/observabilidad|presupuesto|saldo` sin `require_admin`.** El
  gate era `require_module("ia")` y como `ia ∈ INVITADO_MODULES`, el portal
  www podía leer `detalle`/`respuesta` de `ia.llamadas` — las preguntas y
  respuestas literales de las conversaciones de toda la mesa, con el email de
  cada uno, filtrables por `?usuario=` y `?q=`. Violaba la REGLA #8. Lo
  encontraron 5 de 6 cazadores por separado.
- **Orden de chequeo en la rama de service token (`api/auth.py`).** Se
  devolvía el email de `x-acaquant-user-email` **antes** de mirar
  `CF_TRUSTED_SERVICE_TOKENS`, así que cualquier service token válido para el
  AUD (el del portal www, el de la PC de ingesta, un cron) podía afirmar ser
  admin. Ahora la allowlist se chequea primero. **Sólo endurece si
  `CF_TRUSTED_SERVICE_TOKENS` está configurada**: con la env var vacía se
  preserva el comportamiento previo y se loguea un warning, porque cerrar con
  la allowlist vacía dejaría a toda la mesa afuera.

- **Scope de cuenta faltante (BOLA).** `/api/operaciones/comercial/`
  {`portafolio`, `operaciones`, `serie`, `cobros-futuros/cliente`} y
  `/api/back-office/acreencias/cliente` aceptaban cualquier `id_cuenta` y
  devolvían la cartera / las operaciones / los cobros de esa cuenta sin mirar
  si el usuario tenía acceso. Ahora pasan por `verificar_id_cuenta`.
  `/api/operaciones/ops/cuentas-list` recibía el scope y lo **descartaba**
  (heredado del port desde Mongo), devolviendo el padrón completo de
  comitentes con su denominación — o sea los nombres de todos los clientes de
  la mesa — a cualquiera con el módulo `operaciones`. Ahora lo aplica.
- **`POST /oauth/register`** ejecutaba `CREATE SCHEMA/TABLE IF NOT EXISTS` + 2
  `DELETE` en el pool web **en cada request y antes de validar el body**, en un
  path con BYPASS de CF Access (o sea anónimo). Ahora valida primero y el DDL
  corre una única vez por proceso.
- **Rate limiting**: `default_limits` estaba vacío (sólo 4 de ~360 rutas tenían
  techo). Ahora hay un default global deliberadamente holgado —
  600/min y 20000/h por identidad— que frena el runaway sin cortar uso normal.
  Y `_key_by_user` ahora mira `x-acaquant-user-email` primero: antes, como el
  SSR pega con service token, **todos los usuarios compartían un solo bucket**.
- **`/docs`, `/redoc`, `/openapi.json`** quedan cerrados con `ENV=prod`
  (publicaban el mapa completo de los ~360 endpoints). Siguen abiertos en dev.
- **Security headers** (`nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`,
  HSTS) y hardening de `api.service` (`NoNewPrivileges`, `PrivateTmp`,
  `ProtectKernel*`, `RestrictSUIDSGID`).

Pendiente — requiere acción en el Droplet o una decisión con datos de prod:

1. **Configurar `CF_TRUSTED_SERVICE_TOKENS`** en el unit de systemd (hoy el
   default es set vacío). Hasta que esté, el fix del service token no endurece
   nada. Verificar con `python -m scripts.diag_auth_postura`.
2. **Medir la cobertura de grupos.** Los fixes de BOLA son *condicionales*:
   `cuentas_visibles` devuelve `None` (= sin restricción) para admin, para
   quien no está en ningún grupo y ante cualquier excepción de DB. Si
   `manager.grupos` está vacío en prod, el scope no enforcea en ningún lado y
   estos endpoints siguen devolviendo todo — igual que antes, sin romper nada,
   pero sin proteger tampoco. Hay que contar cuántos grupos hay y cuántos
   usuarios no-admin están asignados, y recién ahí decidir si el default pasa a
   ser "sin grupo = no ve nada" (REGLA #2: no tocar sin ese número).
3. **`api.service` corre como `User=root`.** Migrar a un usuario sin
   privilegios implica crear el usuario, mover/chown el venv y revisar los
   paths de logs — hay que hacerlo a mano y verificar el arranque. Lo mismo
   para `ProtectSystem=strict` / `ProtectHome` (hoy romperían: el
   `WorkingDirectory` está en `/root`).

Lo que se auditó y salió **limpio**: no hay secretos commiteados ni `.env`
trackeado; no hay SQL injection (los identificadores dinámicos pasan por
allowlist y los valores van parametrizados); los tres comparadores de token
usan `secrets.compare_digest` sobre bytes; no hay CORS permisivo (no hay CORS
en absoluto, que es la postura correcta acá); las escrituras de agro llevan
`require_no_invitado`; y `/api/ingest/*` es fail-closed sin su token.

**Nota de alcance**: el frontend (`acaquant-web`) NO se auditó — no está en el
checkout ni accesible desde el entorno de la sesión. El borde de seguridad
real es el backend (el front sólo esconde la navegación), pero queda
pendiente revisar del lado del front: que `x-acaquant-portal: guest` se
inyecte server-side sin poder forjarse desde el browser, que el proxy no
reenvíe headers de identidad que vengan del cliente, y que no haya secretos en
bundles `NEXT_PUBLIC_*`.

---

# PARTE B — Credenciales: dónde vive cada una

Qué llave abre qué, dónde vive, y cómo cambiarla. Ninguna de estas va al repo:
viven en `/root/TradingAV/.env` (las lee `config.py` vía `load_dotenv()`), en
algunos systemd unit (`Environment=`), y las del frontend en las env vars de
Vercel.

**Regla:** rotar = regenerar en la fuente → actualizar el `.env` (y Vercel si el
frontend la usa) → reiniciar el servicio afectado. Tras tocar `.env`, la API
necesita `systemctl restart api.service`; los crons toman el cambio solos (cada
run es un proceso nuevo).

---

### 🔴 Críticos (mueven plata o dan acceso amplio)

| Secreto | Qué es / dónde se usa | Cómo rotar | Si se filtra |
|---|---|---|---|
| `ROFEX_USER` / `ROFEX_PASSWORD` / `ROFEX_ACCOUNT` | Credenciales del broker (pyRofex). La API y `motor_ordenes` las usan para **enviar y seguir órdenes reales**. | Portal del broker / pyRofex (cambiar password). | Alguien podría **operar tu cuenta**. Máxima prioridad. |
| `POSTGRES_URI` | Cadena de conexión a Postgres/Supabase (la lee `core/postgres.py`). Acceso total a las DBs SQL. | Supabase → Database → rotar el password del rol → actualizar la URI. | Acceso total a datos de clientes. Rotar + revisar reglas de red. |
| `API_KEY` | Bearer de la API (`api/deps.py`). El frontend la manda en cada request. | Generar un random nuevo → `.env` del backend **y** env var en Vercel (deben coincidir) → restart API + redeploy front. | Acceso a la API saltando el bearer (pero CF Access sigue adelante). |

> **Obsoletos (ya NO se leen — eliminables del `.env`):** `MONGO_URI`, `ATLAS_*`.
> Quedaron del stack Mongo/proveedor,
> decomisado 2026-06-29; el código
> ya no los usa. El secreto vivo de base de datos es `POSTGRES_URI`.

### 🟠 Medios (acceso a datos o a servicios pagos)

| Secreto | Qué es | Cómo rotar |
|---|---|---|
| `AUNESA_CLIENT_ID` / `AUNESA_USERNAME` / `AUNESA_PASSWORD` | Credenciales del custodio (Aunesa) — fuente de movimientos/posiciones. | Coordinar con Aunesa. |
| `BYMA_CLIENT_ID` / `BYMA_CLIENT_SECRET` | OAuth2 para licitaciones primarias BYMA. | Portal BYMA Developer. |
| `MAE_API_KEY` | MarketData MAE (repos/cauciones wholesale). | Coordinar con MAE. |
| `DOLAR_INGEST_TOKEN` | Token de `POST /api/ingest/dolar-oficial` (la PC de oficina lo manda en `X-Ingest-Token`). Va en el `.env` del Droplet **y** en la oficina (deben coincidir). Si se filtra: solo permite escribir el dólar oficial live, no da acceso a la DB. | Random nuevo → `.env` Droplet + oficina → restart API. |
| — (mismo `DOLAR_INGEST_TOKEN`) | También protege `POST /api/ingest/custodia/holdings`: la tenencia de la Caja de Valores, que manda la PC de oficina porque **las APIs de BYMA están detrás de AppGate SDP y el Droplet no las alcanza** (medido). Si se filtra: solo permite reescribir la foto de custodia de una fecha — no da acceso a la DB ni a BYMA (las credenciales de BYMA viven en la PC, no acá). | Ídem fila anterior: es el mismo token. |
| `FINNHUB_API_KEY` | Data de mercado externa. | Dashboard de Finnhub. |
| `RESEARCH_IMAP_USER` / `RESEARCH_IMAP_PASSWORD` / `RESEARCH_MAIL_FROM` | Casilla que recibe el research diario + app password + remitente(s) — los lee `jobs/research_mail.py` (IMAP readonly, QuantAI P6). OJO: la app password da acceso de LECTURA a toda la casilla — usar una app password dedicada, jamás la contraseña real. | Gmail: Cuenta → Seguridad → Contraseñas de aplicaciones → revocar y generar otra → `.env` (el cron la toma solo). |
| `FRED_API_KEY` | API key de FRED (Federal Reserve de St. Louis) — la lee `core/fred_api.py` (tab DATOS INTERNACIONALES de Research, `jobs/fred_research.py`). Gratis, solo lectura de data pública sin cargo: si se filtra, el daño máximo es que un tercero use tu cuota. Sin ella, la tab FRED queda sin datos (todo lo demás sigue igual). | fredaccount.stlouisfed.org/apikeys → regenerar → `.env` (el cron la toma solo; restart API para que sirva la tab). |
| `DEEPSEEK_API_KEY` | Proveedor LLM **default** (`core/modelos.py`). La usan `agente_emisor`, `asistente_despacho` y `asistente_mercado`. Cuenta prepaga, saldo chico: si se filtra, el daño máximo es quemar el saldo. ⚠️⚠️ **Este proveedor SÍ puede entrenar con lo que se le manda**; las tareas con datos del negocio o personales (`asistente_cartera`, `asistente_cliente`, `asistente_operaciones`) salen a OpenAI — ver `config.IA_PERMITE_PROVEEDOR_QUE_ENTRENA`. | platform.deepseek.com → API Keys → regenerar → `.env` → restart API. |
| `OPENAI_API_KEY` | Proveedor LLM para tareas marcadas `datos:"negocio"` — se eligió por su compromiso de NO entrenar con datos de API + borrado a 30 días (decisión user 2026-07-21). El gateway ERA fail-closed para esto; hoy el portazo está aflojado por `config.IA_PERMITE_PROVEEDOR_QUE_ENTRENA` (ver abajo). ⚠️ **LA USAN LAS TAREAS `asistente_cartera`, `asistente_cliente` y `asistente_operaciones`** (`asistente/`, `docs/AvAgentAI.md`; admin-only: la tab LAB del modal del AV AGENT y `scripts/asistente.py`): las marcadas `datos:"negocio"` o `"personal"`, o sea las que ejercen este ruteo de verdad. Sin la key, el asistente no arranca y lo dice. | platform.openai.com → API keys → regenerar → `.env` → restart API. Data controls: sharing en **Disabled**, API call logging **Disabled**, audit logging **Enabled**. |
| `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | LLM del asistente (legacy, no en uso hoy). | Consola Anthropic / Google. |

### 🟢 Config sensible (no son secretos, pero cuidá quién los edita)

| Var | Qué es |
|---|---|
| `CF_ACCESS_TEAM` / `CF_ACCESS_AUD` | Identifican el tenant/app de Cloudflare Access para validar el JWT. Si faltan, el JWT no se valida (modo dev). |
| `CF_TRUSTED_SERVICE_TOKENS` | `common_names` de máquinas confiables (ej. el frontend Vercel). |
| `MANAGER_EMAILS` | Emails admin de bootstrap (fallback al RBAC de `manager.manager_users`). |
| `ENV` | `prod` activa el fail-closed de auth (EXT-AUTH1). |
| `IA_PERMITE_PROVEEDOR_QUE_ENTRENA` | **Ver abajo.** Default `1`. |

### ⚠️ Datos del negocio hacia un proveedor que entrena

**Estado: PERMITIDO.** Decisión del user (2026-09-13), revirtiendo la del
2026-07-21. `config.IA_PERMITE_PROVEEDOR_QUE_ENTRENA` viene en `True`.

| | |
|---|---|
| **Qué apaga** | `core/modelos.py::permitido_salir()` negaba una llamada cuando una tarea marcada `datos: "negocio"` iba hacia un proveedor con `no_entrena: False` (hoy, DeepSeek). Ahora sale, con un `WARNING` en el log y su fila en `ia.llamadas`. |
| **Por qué** | Poder usar DeepSeek por costo mientras el asistente se construye. Palabras del user: *«la restricción la agregaré más adelante cuando esto escale, de momento lo estoy usando solo con cuentas habilitadas y permitidas»*. |
| **Qué acota el alcance igual** | El asistente ve SÓLO las cuentas de `ASISTENTE_CUENTAS` (`asistente/permitido.py`, fail-closed, se editan entrando al Droplet) y la tab es admin-only. |
| **Qué se acepta** | DeepSeek se reserva el derecho de entrenar con lo que se le manda. Los datos de esas cuentas pueden quedar en un modelo de un tercero, y eso no se deshace. |
| **Cuándo volver a `False`** | Cuando el asistente deje de ser una herramienta del admin sobre cuentas elegidas a mano: si `ASISTENTE_CUENTAS` crece, si lo usa alguien más, o si aparece una herramienta que lee la cartera entera. |

⚠️ **El flag afloja `negocio`, nunca `personal`.** La tarea `asistente_cliente`
(contacto, documento, operador del titular) lleva `datos: "personal"`: no sale a
un proveedor que entrena con el flag puesto ni sin él, y `core/traza` no guarda
extracto del pedido ni de la respuesta en `ia.llamadas`. El documento se muestra
recortado a sus últimos dígitos (`asistente/mundos/cliente.py`).

⚠️ **El mecanismo NO se borró**: la ficha `no_entrena` de cada proveedor sigue
declarada en `core/modelos.py` y los dos lugares que la miran —el gateway y el panel
del LAB— leen **la misma constante**. Volver atrás es esa línea, no reconstruir
nada. Un test (`test_el_ruteo_por_proveedor_lo_decide_UNA_constante`) congela que
no aparezca una segunda regla en paralelo.

### Frontend (env vars en Vercel, no en el `.env` del Droplet)

- `API_URL` → apunta a `https://api.acaquant.com`.
- `API_KEY` → debe coincidir con la del backend.
- `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` → service token con el que el
  SSR de acaquant-web pega al backend tras CF Access (`proxy.ts`).

---

### Procedimiento de rotación (general)

1. **Regenerar** la credencial en su fuente (portal del proveedor, Supabase, BotFather…).
2. **Actualizar** donde viva: `.env` del Droplet (`nano /root/TradingAV/.env`) y/o
   env var en Vercel y/o el systemd unit.
3. **Reiniciar** lo afectado: `systemctl restart api.service`. Frontend: redeploy en Vercel.
4. **Verificar** que el servicio levantó OK (`systemctl status`, o un request de prueba).

**Cuándo rotar:** ante sospecha de filtración (alguien vio un `.env`, un token en
un log, etc.), cuando se va alguien del equipo con acceso al servidor, y como
higiene periódica para las críticas (broker, base de datos) — al menos 1 vez al año.

### Higiene

- El `.env` **nunca** se commitea (verificar que esté en `.gitignore`).
- No pegar secretos en logs, chats, ni URLs.
- Revisión de accesos de usuarios: `/manager → USUARIOS` (último acceso + badge
  INACTIVO; ver `docs/RUNBOOK.md`).
