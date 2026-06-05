Colección de la base `ACAPortfolio` (separada del resto del sistema) que guarda las posiciones de cartera de cuentas puntuales para entregar a un proveedor externo. Es la fuente de datos del Partner API: se escribe 2×/día por un job autónomo y se lee de forma read-only desde el servicio externo.

Conecta con: la escribe `jobs/partner_export.py` (exporta posiciones); la lee `partner_api/routes.py` (endpoint `GET /v1/portfolio` en `data.acaquant.com`). No comparte código ni conexión Mongo con la API principal.
