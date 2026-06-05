Único feed live del dólar oficial mayorista (MAE UST$T plazo 000) en la base `Valuaciones`. Lo alimenta un script local desde la PC de oficina; es la fuente en tiempo real del oficial para toda la plataforma.

Conecta con: lo ingesta `api/routers/ingest.py` (endpoint de escritura), centralizado por `core/dolar_oficial.py`; lo leen `api/services/macro.py`, `argy.py`, `engines/curvas.py`, `engines/futuros_dlr.py` y el status del Manager.
