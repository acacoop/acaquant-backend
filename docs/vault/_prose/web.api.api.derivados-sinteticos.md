Proxy live de la tabla de sintéticos (combinaciones LECAP/DLK + futuro DLR): pega a `/api/derivados/sinteticos` y devuelve sin cache para que el polling vea los precios del bono, del futuro y el SPOT actualizados.

Conecta con: vista de sintéticos del front → este route → backend `GET /api/derivados/sinteticos` (service `api/services/sinteticos.py`).
