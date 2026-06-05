Job de ingesta de RSS de medios económicos argentinos (Ámbito, Cronista, iProfesional, Clarín, Infobae, La Nación). Parsea cada feed con feedparser, limpia el HTML del excerpt y guarda las headlines con dedup por URL. Si un feed rompe el XML, lo loggea y sigue con el resto. Corre cada 15 min por cron.

Conecta con: escribe en `News.Headlines` (índice único en `url` + índices por fecha/fuente/categoría), comparte colección con `jobs.news_finnhub`. Lo consume el router `/api/news` (feed agregado + reader mode).
