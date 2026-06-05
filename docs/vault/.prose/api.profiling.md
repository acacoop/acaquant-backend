Middleware opt-in de profiling de requests con pyinstrument. Solo se monta si `config.API_PROFILING` está prendido; entonces cualquier request con `?profile=1` se corre bajo un profiler estadístico y devuelve el árbol de llamadas (HTML) o JSON para speedscope (`?profile=speedscope`), en vez de la respuesta normal. Sin el query param el request pasa derecho (overhead nulo). Sirve para distinguir CPU propio vs espera de I/O de Mongo.

Conecta con: lo monta `api.main` (vía `maybe_add_profiler`) solo con el flag activo; importa pyinstrument de forma perezosa.
