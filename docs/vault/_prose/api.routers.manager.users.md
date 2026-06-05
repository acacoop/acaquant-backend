Sub-router `/api/manager/users` — CRUD de usuarios de la plataforma. GET lista usuarios + roles disponibles; POST crea o upsertea por email; PATCH edita rol/enabled/notes preservando los campos no enviados; DELETE elimina (self-delete permitido). Valida que el rol exista en la matriz vigente. Admin-only.

Conecta con: `core.roles` (`list_users`, `upsert_user`, `delete_user`, `get_matrix`) que persiste en `Manager.Users` y registra cada mutación en `Manager.RoleAudit`; auth `get_user_email` para el actor. Lo consume la tab USUARIOS de la manager-view.
