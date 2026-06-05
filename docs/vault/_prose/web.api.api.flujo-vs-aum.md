Endpoint dual de la vista Flujo vs AuM. Sin `contraparte` devuelve la lista de fondos/contrapartes (`/api/operaciones/fondos`); con `contraparte` (+ `moneda`, default ARS) devuelve las dos series mensuales a comparar: AuM por mes y flujo bruto por mes. Cachea con `s-maxage=300` + stale-while-revalidate.

Conecta con: vista Flujo vs AuM del front → este route → backend `api/routers/operaciones.py` (`/api/operaciones/fondos`, `/api/operaciones/flujo-vs-aum`).
