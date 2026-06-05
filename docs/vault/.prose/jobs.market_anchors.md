Calcula los "anchors" de retorno (cierres de referencia a 7 días, inicio de mes, inicio de año y 1 año atrás) para cada símbolo del watchlist HOME. Fetchea ~13 meses de candle diario (Yahoo para equities/treasuries/índices, frankfurter.app para FX) y guarda el cierre más cercano a cada anchor en el mismo doc de `Market.Quotes`. Luego la API computa los retornos on-the-fly contra el last price.

Cron: 1×/día post-cierre US (22:00 UTC = 19:00 ART, L-V).

Conecta con: usa `core.yahoo.stock_candle` + frankfurter, lee las listas de símbolos de `jobs.market_quotes`, escribe campos `anchor_*` en `Market.Quotes`. Lo consume el watchlist /argy y el home.
