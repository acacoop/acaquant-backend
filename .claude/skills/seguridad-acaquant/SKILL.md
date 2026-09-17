---
name: seguridad-acaquant
description: >
  Usar ANTES de exponer/pushear un endpoint nuevo, un cambio de RBAC, un router,
  o algo que toque auth, secretos, MCP o CF Access. Checklist de seguridad a
  medida de AcaQuant (plata real + auth en capas). Complementa pre-deploy-check
  (que valida imports/ruff/tests, no seguridad). Doc oficial: docs/ACAQUANT.md §7.
---

# security-review — superficie de plata + auth en capas

Capas: Cloudflare Access (quién entra) → API_KEY → JWT → RBAC (`core/roles.py`) →
rate limit. El producto sirve datos financieros de la mesa. Un gate flojo expone
portfolio/cuentas/operaciones de clientes reales.

## Checklist (antes de pushear)

### RBAC / exposición
- ¿El endpoint nuevo tiene su gate? Routers bajo `/api/manager/*` van con
  `require_module(...)` o `require_any_module(...)` en `api/routers/manager/__init__.py`.
  Sin gate explícito → queda ABIERTO a cualquier autenticado.
- ¿Expone datos privados de la mesa (portfolio, AuM, cuentas, operaciones, manager)?
  Esos NO van al MCP server (solo lectura de mercado). Verificar.
- Módulo nuevo: sumar a `MODULES`, actualizar `ENDPOINT_MODULE_PREFIXES` en
  `api/auth.py`, y la matriz en `Manager.RoleMatrix` (la DB pisa el DEFAULT_MATRIX).
- ¿El rol correcto? `operar` (órdenes) es admin-only. Scope de cuenta por grupo
  (`verificar_account`, `api/deps.py`) en endpoints de órdenes/operaciones.

### Secretos
- Cero secretos en código o commits (POSTGRES_URI/`.env` SQL, AUNESA_*, MCP_*, JWT
  secrets). Van en `.env` local / systemd unit files en el Droplet.
- Que el endpoint/job NO logee secretos ni el query con datos sensibles.

### CF Access / MCP (rompen silencioso si se tocan mal — ver project_mcp_cf_access)
- CF Access NO debe tapar `/mcp` ni los `/oauth/*` de token/register/discovery
  (devuelve HTML de login en vez de 401 → muere callado). Solo `/oauth/authorize`
  protegido.
- `TransportSecuritySettings` en `api/mcp/server.py` con `allowed_hosts`/
  `allowed_origins` correctos (sino 421 Misdirected).

### Import-chain (REGLA #1, ya hookeado)
- Un import roto en cualquier router/service montado tumba TODA la API.
  `from api.main import app` debe importar limpio (el hook `check_imports.sh` lo
  bloquea en push, igual validar antes).

## Entrega

Reportar hallazgos por severidad (🔴 bloqueante / 🟡 revisar / 🟢 nota) + la
explicación ejecutiva (REGLA #3). NO pushear con un 🔴 abierto.
