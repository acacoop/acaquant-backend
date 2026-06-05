Proxy live del mercado de caución (repo): reenvía el parámetro opcional `moneda` (ARS/USD) a `/api/cotizaciones/caucion` y devuelve sin cache, porque el motor de caución snapshotea cada 5s y el edge cache pisaba el polling.

Conecta con: vista de caución del front → este route → backend `GET /api/cotizaciones/caucion` (service `api/services/repo.py`, motor `engines/caucion.py`).
