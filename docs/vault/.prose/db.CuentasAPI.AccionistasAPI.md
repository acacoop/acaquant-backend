Copia derivada (read-friendly para la API) del maestro de accionistas/titulares, en la base `CuentasAPI`. Se regenera a partir de la fuente para servir consultas de la API sin tocar la colección original.

Conecta con: la re-sincroniza `jobs/sync_api_copies.py` / `scripts.api_migrate`; la leen `api/routers/cuentas.py`, `api/services/risk.py` y `_cuentas_filter.py`.
