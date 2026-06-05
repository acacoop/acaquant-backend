Sub-router `/api/manager/operaciones` — backfill de `CashFlow.Operaciones` por CSV. La manager-view parsea el CSV en el cliente y manda las filas crudas en lotes a `POST /operaciones/backfill`; el backend las normaliza y upsertea por boleto (idempotente). `GET /operaciones/stats` devuelve el estado actual de la colección para la UI. Admin-only.

Conecta con: service `api.services.operaciones_informes` (normalización + upsert + creación del índice único); escribe `CashFlow.Operaciones` con el cliente rw. Lo consume la tab OPERACIONES de la manager-view.
