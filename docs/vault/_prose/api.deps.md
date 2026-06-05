Dependencias de FastAPI más re-export de los helpers de DB. Aporta `verify_api_key` (valida el header `Authorization: Bearer <API_KEY>`; deja pasar todo si `API_KEY` no está seteada, modo dev) y re-exporta los `get_db_*` de `api/db.py` para que los routers viejos sigan importándolos desde acá.

Conecta con: lee `config.API_KEY`; re-exporta `api.db`; `verify_api_key` se monta global en `api.main`; la postura de auth la valida `api.main._validar_postura_auth`.
