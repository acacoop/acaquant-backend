Tabla compacta de la cadena de opciones GGAL (CALL/PUT), ordenada por valor esperado, con columnas de variación intradía/1D, spread de puntas, IV y griegas (delta, gamma, theta, vega) y volumen. Glosario embebido por columna y selección de contrato para linkear los charts.

Conecta con: recibe la chain por props (datos de `Opciones.Data` vía router `cotizaciones`/`opciones`); emite `onSelect` para alimentar `opcion-historico-chart` y `griegas-historico-chart`. Usa `TableHelp`. Vive en el módulo de opciones / derivados.
