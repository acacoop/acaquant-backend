Modo lectura de un artículo de noticias: dado un URL, trae el texto limpio (reader mode) y lo muestra en un panel con título, autor, fecha y hostname, con fallbacks si la extracción falla.

Conecta con: fetch a `/api/news/article?url=X` (extracción reader-mode del backend, router `news`). Lo abre `news-panel` al clickear un titular.
