Sub-router `/api/manager/roles` — administra la matriz de roles↔módulos (qué ve cada rol) y su audit log. GET devuelve la matriz completa más los módulos canónicos y la lista de roles; PATCH reemplaza los módulos de un rol (filtra módulos desconocidos para no dejar zombies); `/roles/audit` lista los últimos eventos. Admin-only.

Conecta con: `core.roles` (`MODULES`, `get_matrix`, `set_role_modules`, `list_audit`) que persiste en `Manager.RoleMatrix` y registra en `Manager.RoleAudit`; auth `get_user_email` para el actor del audit. Lo consume la tab ROLES Y PERMISOS de la manager-view.
