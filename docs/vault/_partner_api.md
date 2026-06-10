# 🤝 partner_api

9 notas.

- [[partner_api]] — partner_api — API externa de datos de portfolio para un proveedor.
- [[partner_api.auth]] — Auth del partner_api — login usuario/password → JWT, y el guard de los
- [[partner_api.db]] — Conexión Mongo del partner_api — singleton read-only a la base `ACAPortfolio`.
- [[partner_api.main]] — partner_api — app FastAPI del servicio externo de datos para el proveedor.
- [[partner_api.odata]] — partner_api/odata.py — servicio OData v2 sobre ACAPortfolio.Cartera.
- [[partner_api.ratelimit]] — Rate limiter del partner_api — slowapi, keyeado por IP del cliente.
- [[partner_api.routes]] — Endpoints de datos del partner_api — SOLO lectura de ACAPortfolio.Cartera.
- [[partner_api.security]] — Hashing de passwords + emisión/validación de JWT para partner_api.
- [[partner_api.settings]] — Configuración del servicio partner_api — lee su propio set de env vars.
