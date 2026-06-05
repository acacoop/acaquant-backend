Router de consulta del maestro de títulos. `GET /api/titulos/assets` lista activos (unidad, ticker, emisor, cartera, clase, calificación, vencimiento) con filtros simples, y otros endpoints sirven flujos. Cacheado (TTL 600s) porque el maestro cambia poco.

Conecta con: lee las colecciones derivadas `AssetsAPI`/`FlujosAPI` (vía `get_db_titulos`); usa helper `_bonos_cer_fijados` de renta_fija; lo consume el frontend para selectores y fichas de instrumentos.
