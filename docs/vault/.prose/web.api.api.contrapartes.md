Arma la vista de contrapartes: en paralelo pide el flujo de operaciones de los últimos ~2 años (`/api/operaciones/flujo`) y el padrón de contrapartes (`/api/cuentas/contrapartes`), y los devuelve juntos. Trae PII → `Cache-Control: private, no-store`.

Conecta con: vista de contrapartes del front → este route → backend `api/routers/operaciones.py` + `api/routers/cuentas.py`.
