Trae el precio USD live del subyacente (NYSE/Nasdaq) de cada CEDEAR activo y lo upsertea, un doc por ticker. Corre cada 15 min en horario de mercado USA. El scanner de CEDEARs combina este precio live con los cierres EOD para mostrar precio actual + retornos rolling (7d/MTD/YTD).

Conecta con: lee `Trading.Cedears` (underlyings activos), pega a Finnhub vía `core.finnhub.quote`, escribe `Trading.AdrSnapshot`; consumido por `api.services.scanner`. Cron en `deploy/crontab.txt` (`*/15 13-20 L-V`).
