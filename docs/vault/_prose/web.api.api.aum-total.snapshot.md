Devuelve la foto del AuM total de una fecha puntual: una fila por unidad (cartera, tipo, cuenta, valuación, cantidad) más el MEP usado y flag de MEP faltante. Requiere `fecha` (400 si falta); admite filtro por cuenta, operador y moneda. Sin cache.

Conecta con: detalle por día de la vista AuM total del front → este route → backend `GET /api/portfolio/total-snapshot` (service `api/services/portfolio.py`).
