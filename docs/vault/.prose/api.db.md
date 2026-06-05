Helpers de acceso a las bases Mongo sin dependencia de FastAPI (`get_db_trading`, `get_db_valuaciones`, `get_db_cashflow`, `get_db_clientes`, `get_db_manager`, etc.). Existe separado de `api/deps.py` para que la capa de servicios (`api/services/*`) pueda importarlo sin arrastrar fastapi. Todos resuelven sobre el cliente read-only (`SECONDARY_PREFERRED`).

Conecta con: usa el singleton `core.mongo.get_mongo_client_read()`; lo consumen todos los services y varios routers; `api.deps` lo re-exporta por compatibilidad.
