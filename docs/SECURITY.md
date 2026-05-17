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
es la dependency que chequea que el role del email (de `Manager.RoleMatrix`)
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
| `MONGO_URI` | credenciales de Atlas |

Rotación de `API_KEY`: manual — generar nueva, actualizar `.env` del Droplet
+ las env vars de Vercel (acaquant-web), redeploy de ambos.

## Checklist al tocar la API

- Endpoint nuevo → ¿qué grupo de dependency (`_PUBLIC` / `_PORTFOLIOS` / …)?
  Default: el más restrictivo que tenga sentido.
- ¿Expone datos de cuentas/posiciones? → nunca `_PUBLIC`, nunca al MCP.
- ¿Acción mutante o cara? → `@limiter.limit(...)`.
- En prod `API_KEY`, `CF_ACCESS_TEAM` y `CF_ACCESS_AUD` **tienen que** estar
  seteados — sin ellos la auth es fail-open.
- Antes de pushear router/service: REGLA #1 (ver `api/CLAUDE.md`).
- Revisión de cambios con impacto en auth/datos: `/security-review`.
