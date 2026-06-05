Diagnóstico read-only de la salud del cluster Mongo M10 vía la Atlas Admin API. Imprime el CPU por nodo y las slow queries recientes, pero SOLO metadatos (colección, planSummary, docsExamined, duración) — nunca el comando ni valores, redacción por diseño. No toca el cluster. Sirve como paso de "medir antes de cablear el watchdog".
Se corre con `python -m scripts.atlas_health [--min 30]`.
Conecta con: `core.atlas_api` (cpu_por_nodo / slow_queries_meta); env vars `ATLAS_*` del `.env`.
