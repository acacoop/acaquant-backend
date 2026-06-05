Cache in-process para handlers de FastAPI vía decorador `@cached(ttl=N)`. Como la API es un único proceso, alcanza un dict con TTL (sin Redis): ahorra un round-trip a Mongo por request repetido dentro de la ventana. La llave es nombre de función + kwargs; el store está acotado (sweep de expiradas + eviction LRU al pasar 512 entradas) para evitar el leak de RAM que tuvo en producción. No cachea respuestas vacías (negative caching off).

Conecta con: lo importan los routers (`cuentas`, `carteras`, etc.) para envolver endpoints; complementa el caching de la capa de servicios.
