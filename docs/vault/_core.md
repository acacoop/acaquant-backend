# 🧱 core — infraestructura

31 notas.

- [[core]]
- [[core.adhoc_subscriptions]] — Helpers para Trading.AdhocSubscriptions — suscripciones live efímeras.
- [[core.argentina_datos]] — Cliente de argentinadatos.com — indicadores macro AR públicos.
- [[core.atlas_api]] — core/atlas_api.py — lector de la Atlas Admin API (REST de gestión).
- [[core.aunesa]] — core/aunesa.py — cliente único de la API del custodio Aunesa.
- [[core.brackets]] — Brackets — entrada LIMIT + salida automática cuando la entrada se llena.
- [[core.byma]] — Cliente BYMA Primarias Placements.
- [[core.cafci]] — Extracción del código CAFCI desde un string `unidad`.
- [[core.doc_fiscal]] — Documento fiscal (DNI/CUIT/CUIL) — parseo y claves de cruce. Lógica PURA.
- [[core.dolar_oficial]] — Fuente única para el "dólar oficial" mayorista.
- [[core.finnhub]] — Cliente Finnhub con rate limiting interno.
- [[core.grupos]] — core/grupos.py — grupos de acceso por cuenta (scoping multi-tenant).
- [[core.grupos_sql]] — core/grupos_sql.py — lectura del scope de cuentas (grupos) desde Postgres.
- [[core.job_runs]] — Context manager para registrar runs de jobs automáticos en Manager.JobRuns.
- [[core.mae]] — Cliente MAE MarketData.
- [[core.mongo]]
- [[core.mongo_monitor]] — mongo_monitor.py — listener de pymongo para grabar queries de la API.
- [[core.notify]] — Notificaciones operativas (Telegram).
- [[core.openfigi]] — Cliente OpenFIGI con caching en Mongo (Smart.CusipCatalog).
- [[core.pg_mirror]] — core/pg_mirror.py — dual-write best-effort Mongo→Postgres (Fase 2, capa MERCADO).
- [[core.postgres]] — core/postgres.py — conexión a Postgres (Supabase), capa relacional analítica.
- [[core.profiler]] — Stopwatch mínimo para instrumentar pasos dentro de una función.
- [[core.rofex_orders_session]] — Sesión pyRofex dedicada a envío/seguimiento de órdenes.
- [[core.rofex_session]]
- [[core.roles]] — Roles y matriz de permisos por módulo.
- [[core.roles_sql]] — core/roles_sql.py — lecturas de AUTH (roles/matriz) desde Postgres.
- [[core.snapshot_writer]]
- [[core.threads]] — threads.py — lanzamiento de hilos VITALES en motores.
- [[core.tz]] — Zona horaria Argentina — ÚNICO lugar para now / tz / cálculo de frescura.
- [[core.websocket]] — WebSocketManager — conexión WS a pyRofex para los motores de mercado.
- [[core.yahoo]] — Cliente Yahoo Finance vía yfinance (gratis, sin API key).
