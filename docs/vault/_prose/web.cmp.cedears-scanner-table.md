Tabla del Scanner de Renta Variable con switch CEDEAR/ADR. En CEDEAR muestra precio BYMA en ARS y métricas live del `motor_cedears`; en ADR muestra el precio NYSE del subyacente en USD y retornos EOD (1d/7d/MTD/YTD). Todas las columnas son ordenables; default top-movers arriba.

Conecta con: recibe `CedearScannerRow[]` desde el scanner (service `scanner.py`, fuentes `motor_cedears` live + `Trading.PreciosAcciones` EOD); emite selección de ticker al contenedor.
