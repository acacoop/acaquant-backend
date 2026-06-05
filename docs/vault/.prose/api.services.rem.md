Capa de servicio del REM (Relevamiento de Expectativas de Mercado del BCRA). Lista los informes disponibles, devuelve la serie de expectativas de un informe (mediana/promedio/percentiles) y calcula el breakeven de inflación acumulado: encadena el IPC mensual esperado en un promedio geométrico desde hoy hasta cada mes futuro, listo para superponer al breakeven de mercado en el chart. Funciones cacheadas.

Conecta con: lee `Trading.REM` (poblada por `jobs.argentina_datos._ingestar_rem`, filtrada a IPC INDEC nivel general). Lo invocan el router de cotizaciones/analítica y la tool MCP de REM.
