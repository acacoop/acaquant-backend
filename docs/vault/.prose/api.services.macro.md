Capa de servicio de series macro: devuelve cada variable (tamar, cer, dólar, badlar, mep, o `<TICKER>.<CAMPO>`) como valor actual + serie histórica + estadísticos + una clasificación textual ("alto/bajo/normal"). Algunas variables están bloqueadas por falta de data y devuelven un stub con hint. Funciones cacheadas (`@cached`).

- `obtener_serie_macro` y `clasificar_nivel` son las dos tools que consume el asistente/analítica.

Conecta con: lee `Trading.<BADLAR/CER/…>` y `Trading.MarketSnapshot.metrics`, usa `quant.stats` para los estadísticos; lo invocan los routers `/api/cotizaciones/*` y `/api/analitica`.
