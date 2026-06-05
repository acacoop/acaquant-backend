Endpoint de identidad del usuario logueado. `GET /api/me` devuelve `{email, role, modules, is_admin}` para que el frontend sepa qué links del nav mostrar, a dónde redirigir (403) y si pinta la sección admin. Sin gate de módulo: cualquier usuario ya autenticado por Cloudflare Access puede consultar su propia identidad.

Conecta con: toma el email de `api.auth.get_user_email` (JWT de Cloudflare Access) y resuelve role+módulos con `core.roles`; lo consumen el nav y `proxy.ts` del frontend.
