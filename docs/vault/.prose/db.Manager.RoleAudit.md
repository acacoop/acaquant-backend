Log de auditoría de cambios en la matriz de roles, en la base `Manager`. Registra quién modificó qué permiso de qué rol y cuándo, para trazabilidad del control de acceso.

Conecta con: la escriben `core/roles.py` y `api/routers/manager/users.py` ante cada cambio de roles/usuarios; se consulta desde el sub-router de roles del Manager.
