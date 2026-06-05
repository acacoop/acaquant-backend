Vista Scanner del módulo Renta Variable: un row por CEDEAR activo con su categoría (sector/industria/región/país) y métricas operativas (last, intradía %, vs 1D %, y retorno "real" en USD descontando la devaluación implícita del CCL). Lee el CCL una sola vez por request y lo aplica a todos los tickers. Cacheado 5s para soportar polling del frontend.

Conecta con: joina `Trading.Cedears` (master categórico) con `Trading.CedearsSnapshot` (live de `engines.motor_cedears`, cada 1s) y usa el CCL live de `Valuaciones.DolarSnapshot`/`Dolar`. Lo invoca el router `/api/scanner`.
