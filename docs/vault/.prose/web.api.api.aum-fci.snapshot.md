Devuelve la foto de tenencias FCI de una fecha puntual: una fila por unidad (emisor, ticker, cuenta, valuación, cantidad). Requiere `fecha` (400 si falta) y admite filtro por cuenta y operador. Sin cache.

Conecta con: tab FCI / detalle por día de la vista AuM en el front → este route → backend `GET /api/portfolio/fci-snapshot` (service `api/services/portfolio.py`).
