---
id: web.lib.proxy
type: lib
layer: web-lib
repo: frontend
tags: [lib, web-lib, frontend]
path: src/proxy.ts
---

# web/proxy

**Archivo:** `src/proxy.ts`

## Qué hace
El proxy de Next.js (el ex-middleware) es el portero de las rutas restringidas del frontend: intercepta navegaciones y llamadas a /api/* en paths sensibles (manager, operar, operaciones, aum, valuaciones, back-office, renta-variable) y decide quién pasa según el role del usuario. Resuelve el email de confianza desde el sello firmado de Cloudflare Access, sanitiza headers entrantes para evitar spoofeo, consulta /api/me del backend (con cache en memoria de 30s por email) y exige que el usuario tenga alguno de los módulos requeridos por ese path; si no, redirige al home o devuelve 403/502. Falla cerrado en prod para evitar el flash de páginas restringidas. El mapeo path→módulo debe mantenerse en sync con ENDPOINT_MODULE_PREFIXES de api/auth.py.
Conecta con: src/lib/cf-access.ts (trustedEmail), el endpoint /api/me del backend (api.routers.me) y el RBAC de core.roles / api.auth; es la primera línea de defensa antes de que cualquier route handler de Next reciba la request.

## Usa / conecta con →
- [[api.routers.cuentas]]  ·  _module_
- [[api.routers.manager]]  ·  _module_
- [[api.routers.operaciones]]  ·  _module_
- [[api.routers.operar]]  ·  _module_
- [[api.routers.operativa]]  ·  _module_
- [[api.routers.ordenes]]  ·  _module_
- [[api.routers.risk]]  ·  _module_
- [[api.routers.scanner]]  ·  _module_
- [[api.routers.titulos]]  ·  _module_
- [[web.lib.cf-access]]  ·  _lib_
