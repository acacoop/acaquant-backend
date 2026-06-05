Paquete `core/` — capa de infraestructura del backend. Agrupa los clientes externos (Aunesa, BYMA, Finnhub, Yahoo, argentinadatos, MAE, Atlas), el acceso a Mongo y los helpers transversales (roles, grupos, job_runs, snapshot_writer, websocket). Su `__init__.py` está vacío: solo marca el paquete.

Regla dura: `core/` no importa nada del resto del proyecto salvo `config` — es la base sobre la que se apoyan `engines/`, `jobs/` y `api/services/`.

Conecta con: lo importan engines, jobs y api/services; no depende de ellos.
