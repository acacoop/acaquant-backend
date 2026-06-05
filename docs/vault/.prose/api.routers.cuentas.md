Router `/api/cuentas`: listados maestros de cuentas para selectores de la UI. `/accionistas` lee `CuentasAPI.AccionistasAPI`; `/contrapartes` lee directo de `CashFlow.Contrapartes` (nombre = contraparte, grupo = segmento), sin la copia intermedia. Ambos endpoints cacheados 1 hora.

Conecta con: lee `CuentasAPI.AccionistasAPI` (vía `api.db.get_db_cuentas`) y `CashFlow.Contrapartes` (vía `get_db_cashflow`); usa `api.cache.cached`; lo monta `api.main`.
