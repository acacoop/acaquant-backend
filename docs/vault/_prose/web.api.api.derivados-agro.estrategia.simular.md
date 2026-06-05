POST proxy del simulador de estrategias agro: valida el body como JSON y lo reenvía al backend, que calcula el escenario. El gate de admin lo aplica el backend. Sin cache.

Conecta con: simulador de estrategias de la vista agro del front → este route → backend `POST /api/derivados/agro/estrategia/simular` (router `api/routers/derivados_agro.py`).
