"""partner_api — API externa de datos de portfolio para un proveedor.

Servicio FastAPI SEPARADO de `api.acaquant.com` (proceso propio, puerto
propio). Sólo lee `ACAPortfolio.Cartera` — una colección dedicada que
contiene únicamente las cuentas de `config.PARTNER_EXPORT_CUENTAS`,
poblada por el cron `jobs/partner_export.py`.

Aislamiento (ver docs/PARTNER_API.md):
- Conexión Mongo propia con un usuario read-only scopeado a la base
  `ACAPortfolio` — no puede tocar `Valuaciones`, `Manager`, etc.
- Auth propia (usuario/password → JWT), independiente de Cloudflare
  Access de la mesa.
- Si este servicio cae o lo atacan, `api.acaquant.com` no se entera.
"""
