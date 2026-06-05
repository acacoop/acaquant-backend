Paquete `api/services` — marcador del paquete (archivo `__init__.py` vacío). Agrupa toda la capa de lógica de negocio pura de la API: services invocables tanto por los routers HTTP como por scripts, sin dependencia de FastAPI. Cada módulo hermano (portfolio, renta_fija, ordenes, pnl, etc.) resuelve un dominio.

Conecta con: lo importan los routers de `api/routers/`; los services adentro leen/escriben Mongo y pegan a fuentes externas; no contiene lógica propia.
