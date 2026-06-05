Proxy live de la tabla PASE AGRO (Trigo/Maíz/Soja Rosario): pega a `/api/derivados/agro` y devuelve sin cache para que el polling vea cambios en tiempo real (last_price, oficial, pizarra editada).

Conecta con: vista Pase Agro del front → este route → backend `GET /api/derivados/agro` (service `api/services/derivados_agro.py`, motor `engines/motor_agro.py`).
