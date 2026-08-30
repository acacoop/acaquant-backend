# SECURITY.md — postura de seguridad de la API

Doc consolidado de cómo se protege `api.acaquant.com`. La seguridad está en
**capas**: cada request pasa por todas las que apliquen. Código fuente de
verdad: `api/auth.py`, `api/deps.py`, `api/main.py`, `api/ratelimit.py`,
`core/roles.py`.

## Las capas (en orden, de afuera hacia adentro)

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

## 1. Cloudflare Access — quién entra

CF Access protege los hostnames (`trading.acaquant.com`, `api.acaquant.com`).
Es el IdP: login por OTP/email.

⚠️ **PENDIENTE — la app `acaquant-mcp-bypass` sigue en el panel de Access.**
(BYPASS + Everyone) exime 5 paths sin login — `/mcp`, `/oauth/token`,
`/oauth/register`, `/.well-known/oauth-*` — para un MCP server que **se borró
del repo el 2026-08-28**. Hoy apuntan a 404s, así que no exponen nada, pero
ocupan **5/5 destinations**, la cuota entera, y volverían a estar vivos el día
que algo se monte en esos paths. Sacarla es una acción en Cloudflare, no en
este repo: por eso queda escrito acá y no se borra hasta hacerlo.

## 2. API_KEY — el frontend autorizado

`api/deps.py::verify_api_key` exige `Authorization: Bearer <API_KEY>`. Es el
secreto compartido entre `acaquant-web` (server-side) y la API. Se aplica
como dependency `_PUBLIC` a casi todos los routers en `api/main.py`.

- **`/api/health`** y **`/api/me`** NO llevan el gate (health es trivial;
  `/api/me` devuelve la identidad propia del que llama).

⚠️ **Fail-open dev**: si `API_KEY` no está en `.env`, `verify_api_key` deja
pasar todo. En prod (Droplet) `API_KEY` **debe** estar seteada.

## 3. JWT de Cloudflare Access — identidad real

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

## 4. RBAC — qué ve cada role

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

## 5. Rate limiting

`api/ratelimit.py` — slowapi, `SlowAPIMiddleware`. La key es el **email del
usuario** sacado del JWT firmado por CF (`cf-access-jwt-assertion`, no
spoofable); los anónimos comparten **un solo bucket** (`anon`) para que un
atacante no-autenticado no pueda quemar cuota por volumen. Límites por
endpoint vía `@limiter.limit(...)` (ej. `/api/chat`, `/manager/jobs/run`).

## Secretos / env vars

Viven en `.env` (local) y systemd unit files (Droplet). Nunca en el repo.

| Var | Qué protege |
|---|---|
| `API_KEY` | gate Bearer del frontend ↔ API |
| `CF_ACCESS_TEAM` / `CF_ACCESS_AUD` | validación del JWT de CF Access |
| `CF_TRUSTED_SERVICE_TOKENS` | service tokens aceptados (acaquant-web SSR) |
| `POSTGRES_URI` | credenciales de Postgres/Supabase |

Rotación de `API_KEY`: manual — generar nueva, actualizar `.env` del Droplet
+ las env vars de Vercel (acaquant-web), redeploy de ambos.

## Checklist al tocar la API

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
- Revisión de cambios con impacto en auth/datos: `/security-review`.

## Verificar la superficie HTTP

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

## Auditoría 2026-08-03 — backlog pendiente

Auditoría de código sobre las 361 rutas (6 dimensiones, cada hallazgo
verificado de forma adversarial). **No hubo pentest contra prod** — todo lo de
abajo sale de leer el código, y lo que depende de datos reales está marcado
como no verificado (REGLA #2).

Ya corregido en el commit de la auditoría:

- **`GET /api/ia/observabilidad|presupuesto|saldo` sin `require_admin`.** El
  gate era `require_module("ia")` y como `ia ∈ INVITADO_MODULES`, el portal
  www podía leer `detalle`/`respuesta` de `ia.trazas` — las preguntas y
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
