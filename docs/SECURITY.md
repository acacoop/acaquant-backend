# SECURITY.md — postura de seguridad de la API

Doc consolidado de cómo se protege `api.acaquant.com`. La seguridad está en
**capas**: cada request pasa por todas las que apliquen. Código fuente de
verdad: `api/auth.py`, `api/deps.py`, `api/main.py`, `api/ratelimit.py`,
`api/mcp/`, `core/roles.py`.

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
Es el IdP: login por OTP/email. **Excepción**: la app `acaquant-mcp-bypass`
(BYPASS + Everyone) exime 5 paths para que el MCP funcione — `/mcp`,
`/oauth/token`, `/oauth/register`, `/.well-known/oauth-*` (ver `docs/MCP.md`).
`/oauth/authorize` SÍ queda protegido. Esos 5 paths definen su propia auth.

## 2. API_KEY — el frontend autorizado

`api/deps.py::verify_api_key` exige `Authorization: Bearer <API_KEY>`. Es el
secreto compartido entre `acaquant-web` (server-side) y la API. Se aplica
como dependency `_PUBLIC` a casi todos los routers en `api/main.py`.

- **`/api/health`** y **`/api/me`** NO llevan el gate (health es trivial;
  `/api/me` devuelve la identidad propia del que llama).
- Los paths del MCP/OAuth tampoco — definen su auth aparte.

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

## MCP — auth propia

`api/mcp/` se monta en `/mcp` solo si hay `MCP_BEARER_TOKEN` (static, dev/
curl) o `MCP_JWT_SECRET` (OAuth 2.1 + PKCE + DCR, prod). `MCPBearerMiddleware`
gatea `/mcp/*`. Expone **32 tools de SOLO LECTURA** de mercado — NO portfolio,
cuentas, AuM, operaciones ni manager (datos privados de la mesa, excluidos a
propósito). Flow completo: `docs/MCP.md`.

## Secretos / env vars

Viven en `.env` (local) y systemd unit files (Droplet). Nunca en el repo.

| Var | Qué protege |
|---|---|
| `API_KEY` | gate Bearer del frontend ↔ API |
| `CF_ACCESS_TEAM` / `CF_ACCESS_AUD` | validación del JWT de CF Access |
| `CF_TRUSTED_SERVICE_TOKENS` | service tokens aceptados (acaquant-web SSR) |
| `MCP_BEARER_TOKEN` | fallback static del MCP |
| `MCP_JWT_SECRET` | firma de los JWT OAuth del MCP |
| `POSTGRES_URI` | credenciales de Postgres/Supabase |

Rotación de `API_KEY`: manual — generar nueva, actualizar `.env` del Droplet
+ las env vars de Vercel (acaquant-web), redeploy de ambos.

## Checklist al tocar la API

- Endpoint nuevo → ¿qué grupo de dependency (`_PUBLIC` / `_PORTFOLIOS` / …)?
  Default: el más restrictivo que tenga sentido.
- ¿Expone datos de cuentas/posiciones? → nunca `_PUBLIC`, nunca al MCP.
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

Pendiente, por orden de prioridad:

1. **Configurar `CF_TRUSTED_SERVICE_TOKENS`** en el unit de systemd (hoy el
   default es set vacío). Hasta que esté, el fix de arriba no endurece nada.
   Verificar con `python -m scripts.diag_auth_postura`.
2. **Scope de cuenta faltante (BOLA).** `/api/operaciones/comercial/*`
   (`portafolio`, `operaciones`, `serie?id_cuenta`, `cobros-futuros/cliente`),
   `/api/operaciones/ops/cuentas-list` (recibe el scope y lo descarta) y
   `/api/back-office/acreencias/*` aceptan `id_cuenta` sin pasar por
   `verificar_account`. Un usuario con el módulo lee cuentas fuera de su grupo.
   **Impacto real desconocido**: `core/grupos.py::cuentas_visibles` devuelve
   `None` (= sin restricción) para admin, para quien no está en ningún grupo y
   ante cualquier excepción — si `manager.grupos` está vacío en prod, el scope
   no está enforceando en ningún lado. **Medir primero** cuántos grupos y
   usuarios asignados hay antes de decidir.
3. **Rate limiting.** Cubre 4 de 361 rutas y `default_limits=[]`. Además
   `_key_by_user` (`api/ratelimit.py`) deriva la clave de headers crudos
   (`jwt[-16:]` o el email sin validar) → rotar el header da bucket nuevo, y
   todos los requests del SSR comparten uno solo porque ignora
   `x-acaquant-user-email`. Poner un default global y keyear por identidad ya
   resuelta.
4. **`POST /oauth/register`** (path con BYPASS de CF Access, sin auth ni rate
   limit) ejecuta DDL + 2 `DELETE` en el pool web de Postgres antes de validar
   el body. Mover el `_ensure_sql()` al arranque y ponerle rate limit.
5. **Tokens del MCP**: no miran el RBAC y no se revocan al deshabilitar un
   usuario en Manager.
6. **`/docs`, `/redoc`, `/openapi.json`** están sin auth (los cubre CF Access,
   pero publican el mapa completo de los 361 endpoints). Cerrarlos en prod con
   `docs_url=None` si `ENV=prod`.
7. **`api.service` corre como root** sin hardening de systemd
   (`NoNewPrivileges`, `ProtectSystem`, `PrivateTmp`, `User=`).
8. Sin security headers (HSTS, `X-Content-Type-Options`, `X-Frame-Options`) —
   impacto bajo siendo una API JSON, pero es higiene barata.

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
