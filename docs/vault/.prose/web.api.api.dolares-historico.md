Proxy del histórico de dólares (MEP + CCL + oficial) que alimenta el chart custom del panel ARGY: propaga la querystring a `/api/cotizaciones/historico/dolares` y devuelve sin cache (la cadencia la decide el polling del cliente).

Conecta con: chart de dólares del panel ARGY en el front → este route → backend `GET /api/cotizaciones/historico/dolares` (service `api/services/argy.py`).
