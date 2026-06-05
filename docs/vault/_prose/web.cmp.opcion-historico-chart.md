Histórico de precio de un contrato de opción individual (call o put GGAL): plotea `last` vs tiempo sobre las operaciones de los últimos 21 días, con línea de referencia opcional del último precio live. Usa índice como eje X para no abrir huecos en fines de semana.

Conecta con: fetch a `/api/cotizaciones/historico/opciones?instrumento=X` (lee `Opciones.Data`). Se linkea al contrato seleccionado en la tabla de opciones.
