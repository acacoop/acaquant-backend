Módulo de constantes/helpers compartidos entre los sub-routers de `manager/`. Define `_AR_TZ` (timezone America/Argentina/Buenos_Aires, para formatear timestamps en hora local) y `PROJECT_ROOT` (raíz del repo, usado como cwd del subprocess que dispara `jobs/run`).

Conecta con: lo importan `manager/jobs.py`, `manager/status.py` y `manager/options.py`. Sin lógica de negocio ni acceso a Mongo.
