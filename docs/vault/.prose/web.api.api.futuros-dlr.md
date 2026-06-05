Proxy live de los futuros DLR (Dólar A3500): pega a `/api/cotizaciones/futuros-dlr` y devuelve sin cache, porque el edge cache pisaba el polling del cliente.

Conecta con: vista de futuros DLR del front → este route → backend `GET /api/cotizaciones/futuros-dlr` (motor `engines/futuros_dlr.py`, colección `Trading.FuturosDLRSnapshot`).
