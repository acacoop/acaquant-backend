Panel de administración de grupos de acceso por cuenta (scoping multi-tenant): un grupo agrupa usuarios (emails) + cuentas. Permite crear/editar grupos. Regla: usuario sin grupo ve todo; usuario en ≥1 grupo ve solo las cuentas de sus grupos; el admin ve todo siempre.

Conecta con: fetch a `/api/manager/grupos` (CRUD, respaldado por `core.grupos` → colección de grupos) y `/api/manager/users` para el listado de emails. Se monta como tab dentro de `manager-view`.
