Arma la vista de cashflow: en paralelo pide los flujos de los últimos ~2 años (`/api/operaciones/flujos`) y el padrón de accionistas (`/api/cuentas/accionistas`), y los devuelve juntos. Como trae PII de clientes, fuerza `Cache-Control: private, no-store` (el backend ya cachea 300s para ahorrar Mongo).

Conecta con: vista de cashflow del front → este route → backend `api/routers/operaciones.py` + `api/routers/cuentas.py`.
