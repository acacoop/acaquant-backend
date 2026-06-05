Proxy live de la tabla Mejoras Precio Disponible (3 bloques: Soja/Maíz/Trigo + LECAPs): pega a `/api/derivados/agro/mejoras-dispo` y devuelve sin cache para que el polling vea precios y TNAs frescos.

Conecta con: panel Mejoras Dispo de la vista agro del front → este route → backend `GET /api/derivados/agro/mejoras-dispo` (service `api/services/mejoras_dispo.py`).
