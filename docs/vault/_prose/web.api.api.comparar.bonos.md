Devuelve el universo de bonos disponibles para el selector del comparador (pega a `/api/analitica/comparar/bonos`). Sin cache de edge; el cliente refetcha al montar (TTL backend 30s).

Conecta con: selector del tab "Comparar Inversión" del front → este route → backend `GET /api/analitica/comparar/bonos` (service `api/services/comparar_inversion.py`).
