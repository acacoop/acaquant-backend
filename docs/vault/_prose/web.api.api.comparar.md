Proxy del comparador de inversiones: propaga la querystring a `/api/analitica/comparar` del backend y devuelve sin cache (precios y MEP se mueven cada ~10s).

Conecta con: tab "Comparar Inversión" del front → este route → backend `GET /api/analitica/comparar` (service `api/services/comparar_inversion.py`).
