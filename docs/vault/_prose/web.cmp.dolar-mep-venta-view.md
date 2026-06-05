Vista de venta de dólar MEP (espejo de la de compra): el input es el monto USD a vender y la operativa dispara BUY AL30D (cancela short) + SELL AL30 (cierra long) para devolver ARS. La tabla del día muestra todas las operativas, distinguidas por el campo `tipo`.

Conecta con: postea a `/api/operativa/mep/venta` (service `api.services.operativa_mep`); usa `web.cmp.dolar-mep-board` y `dolar-mep-detalle-drawer`; recibe estado del shell por props.
