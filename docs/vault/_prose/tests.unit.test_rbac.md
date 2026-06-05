Golden tests del RBAC (`core/roles.py`) que congelan la matriz de permisos crítica: `operar` y `manager` quedan admin-only, sales no ve datos privados (operaciones/portfolios), y la matriz solo usa módulos canónicos. Verifica que `has_access` falle cerrado: niega módulos fuera del role, módulos inexistentes (anti-typo) y roles sin entrada en la matriz (anon/service token). Si alguien afloja un gate por accidente, salta acá.

Conecta con: importa `core.roles` (DEFAULT_MATRIX, MODULES, has_access); red de seguridad del control de acceso por módulo de toda la API.
