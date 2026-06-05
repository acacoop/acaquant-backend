Precalienta la cache in-process de la vista COMERCIAL. Como esa cache (`@cached`) vive dentro del proceso uvicorn de la API, un cron común no la calentaría (corre en otro proceso): este job pega por HTTP al uvicorn local (127.0.0.1:8000) para que la cache se popule dentro de la API. Por cada operador llama operador + serie(volumen) + serie(aum).

Read-only (solo GET). Pensado para correr cada ~4-5 min en horario de mercado (TTL de cache = 300s), así los operadores la agarran caliente.

Conecta con: pega a `GET /api/operaciones/comercial/operadores`, `/operador` y `/serie` del uvicorn local. Usa RBAC con un email de `MANAGER_EMAILS`. No toca Mongo directo.
