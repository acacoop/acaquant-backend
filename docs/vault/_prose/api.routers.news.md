Router de noticias. `GET /api/news` lista headlines de RSS agregados (filtrables por fuente/categoría/keyword/fecha, paginados), `/article` baja la nota y la limpia con trafilatura para un "reader mode" inline (cache 1h en memoria, rate-limited), y `/stats` agrega conteo por fuente. El fetch del reader tiene guard anti-SSRF que revalida cada redirect (bloquea IPs privadas/loopback/metadata cloud).

Conecta con: lee `News.Headlines` (poblada por `jobs.news_ingesta` / `jobs.news_finnhub`); el reader pega a URLs externas con validación propia; lo consume el panel de noticias del frontend.
