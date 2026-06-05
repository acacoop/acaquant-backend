PATCH de un cereal puntual de la Cámara: recibe `{precio_ars?, precio_usd?}`, valida que sea JSON y reenvía al backend, que aplica la validación y el audit del cambio.

Conecta con: edición inline del panel Cámara del front → este route → backend `PATCH /api/derivados/agro/camara/{cereal}` (service `api/services/camara_cereales.py`).
