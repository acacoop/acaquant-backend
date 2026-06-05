Cronómetro mínimo (`Stopwatch`) para instrumentar pasos dentro de una función y ver dónde se va el tiempo. Se marca cada hito con `.step("nombre")` y `.done()` devuelve un dict con el total y el desglose por paso en milisegundos. Deliberadamente plano (sin pasos anidados).

Conecta con: lo usan endpoints/services pesados (ej. la vista AuM) para emitir un trace de timings que se muestra en el modo profiling. No toca Mongo ni red. Complementa el middleware de `api.profiling` y el `core.mongo_monitor`.
