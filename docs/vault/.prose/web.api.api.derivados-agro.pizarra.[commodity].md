PATCH proxy de la pizarra (precios manuales) de un commodity agro: valida el body como JSON y lo reenvía al backend. El gate trader+admin lo aplica el backend.

Conecta con: edición de pizarra de la vista agro del front → este route → backend `PATCH /api/derivados/agro/pizarra/{commodity}` (router `api/routers/derivados_agro.py`).
