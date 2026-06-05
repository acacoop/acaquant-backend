Job de precompute del PnL de TODAS las cuentas (~880). La vista TOTALES recorría todas las cuentas en vivo por request y se pasaba del timeout (502); este job hace ese cálculo offline y lo persiste, un documento por cuenta con sus filas y el detalle de boletos. Swap atómico (sin ventana de vacío). Corre cada 30 min (:05 y :35), después de `negocio_movimientos`.

Conecta con: invoca `api.services.pnl.pnl_todas_cuentas_compute` (motor de PnL cost-basis), escribe `Valuaciones.PnLTotalesCache` vía `reemplazar_coleccion_atomico`. Esa colección la lee el endpoint `/api/portfolio/pnl-todas`.
