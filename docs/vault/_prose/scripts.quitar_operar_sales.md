Migración one-shot de RBAC: saca el módulo `operar` del rol `sales` en la matriz viva (Manager.RoleMatrix). El default en código ya lo tiene como admin-only, pero la matriz persistida lo pisaba y un sales podía enviar/cancelar órdenes. Solo toca `sales`, no trader ni admin. Idempotente (si ya no lo tiene, no-op) y por defecto dry-run; con --apply escribe vía set_role_modules (deja audit log e invalida cache). Uso: `python -m scripts.quitar_operar_sales [--apply]`.

Conecta con: core.roles (get_matrix/set_role_modules), Manager.RoleMatrix, Manager.RoleAudit.
