Job de rollup diario de opciones: agrupa todos los ticks intradía de `Opciones.Data` en una fila por (fecha, symbol) con OHLC del día (high/low/last), EV máximo y los griegos/IV/spot del último tick. Idempotente por upsert. Modos: día actual (cron 20:15 UTC L-V), `--fecha` puntual o `--backfill` de todo el histórico disponible.

Conecta con: lee `Opciones.Data` (ticks del motor de opciones GGAL), escribe `Opciones.DataHistorica`. Esta serie histórica la consume el módulo de opciones (`api.services.opciones`).
