Poller centralizado que llena `Market.Quotes` con el último snapshot por símbolo (equities/ETFs/índices vía Finnhub, forex vía frankfurter.app, futuros/cripto vía Yahoo). Patrón eficiente: un solo poller alimenta Mongo y todos los clientes (home, /renta-variable, asistente) leen de ahí — cero hammering extra sobre Finnhub aunque haya muchos tabs abiertos.

Cron: cada 1 min en horario de mercado US (L-V); flag `--extra` para la lista ampliada.

Conecta con: usa `core.finnhub.quote` + `core.yahoo`, escribe `Market.Quotes`. Define las listas de símbolos (HOME_STOCKS/FX/etc.) que reusa `jobs.market_anchors`. Lo consumen los watchlists del front.
