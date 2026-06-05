Copia derivada (read-friendly para la API) del maestro `CashFlow.Contrapartes`, en la base `CuentasAPI`. Sirve consultas rápidas de contrapartes a la API y a los filtros de cuenta.

Conecta con: la re-sincroniza `jobs/sync_api_copies.py` post-job fuente; la leen `api/routers/cuentas.py`, `api/routers/operaciones.py`, `api/services/risk.py`, `jobs/aum.py` y `jobs/_aum_filters.py` (filtros de exclusión del AuM).
