Devuelve la serie temporal del AuM de FCI: total por fecha y desglose por emisor, en un rango `desde`/`hasta`, con filtro opcional por cuenta y operador. No cachea (el cron actualiza diario y la cartera se edita durante el día).

Conecta con: tab FCI de la vista AuM en el front → este route → backend `GET /api/portfolio/fci-serie` (service `api/services/portfolio.py`).
