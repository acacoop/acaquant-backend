Rate limiter compartido del servicio, basado en slowapi y keyeado por la IP real del cliente. Detrás de Cloudflare+nginx, la IP verdadera viene en `CF-Connecting-IP` (helper `client_ip`); si no, cae a la IP de la conexión. Limita tanto la fuerza bruta sobre `/v1/token` como el martilleo de los endpoints de datos (límite por defecto 120/hora).

Conecta con: exporta `limiter` y `client_ip`, usados por `partner_api.auth`, `partner_api.routes` y `partner_api.main` (registra el handler de `RateLimitExceeded`).
