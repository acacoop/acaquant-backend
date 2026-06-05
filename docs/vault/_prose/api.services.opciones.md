Capa de servicio de opciones GGAL: arma la chain (strikes con bid/offer/last/greeks/IV), la metadata, los trades históricos y permite actualizar la tasa libre de riesgo. Filtra solo opciones con tick del día (las ilíquidas conservan precio viejo y se descartan). Casi todo es solo-lectura; la única escritura es la tasa.

Conecta con: lee la DB `Opciones` (poblada por `engines.options` vía WS y `jobs.options_rollup` al cierre); `update_opciones_tasa` escribe en `Opciones.Metadata.config` y limpia el cache. Lo invoca el router de opciones del módulo derivados.
