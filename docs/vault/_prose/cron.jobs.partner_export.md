Cron en 2 corridas diarias (21:30 UTC L-V y 02:00 UTC del día siguiente) que corre `jobs.partner_export`: exporta las posiciones de cuentas puntuales a la base del servicio externo.

Conecta con: ejecuta `jobs/partner_export.py`; lee posiciones/valuaciones internas y escribe a `ACAPortfolio.Cartera`, la base que sirve la `partner_api` al proveedor externo. Timeout 20m.
