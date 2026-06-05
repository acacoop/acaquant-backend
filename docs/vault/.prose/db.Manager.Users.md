Maestro de usuarios de la plataforma en la base `Manager`: identidad (email), rol asignado y el mapeo operador↔usuario que permite resolver cuentas huérfanas en el Tablero Comercial.

Conecta con: la lee `api/auth.py` (resuelve rol del caller tras validar el JWT de Cloudflare Access), `core/roles.py` y `api/services/comercial.py`; se administra (CRUD) vía `api/routers/manager/users.py`.
