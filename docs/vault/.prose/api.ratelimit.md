Rate limiter compartido de la API: instancia única de slowapi keyeada por identidad del usuario. La key sale del JWT firmado de Cloudflare Access (no spoofable, usa sus últimos 16 chars como proxy), con fallback al header de email y, en último caso, a la IP. Los anónimos comparten un único bucket para que nadie sin autenticar queme cuota por volumen.

Conecta con: lee headers de CF Access; lo monta `api.main` en `app.state.limiter`; los routers aplican `@limiter.limit(...)` sobre endpoints sensibles (requieren `request: Request` como primer arg).
