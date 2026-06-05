One-shot inicial que puebla `Trading.CanjeCierre` con el histórico, para que la serie del canje CCL/MEP intra-bono tenga datos desde el día 1 (el cron `jobs/cierre_canje` solo persiste de hoy en adelante). Acá sí usa el aggregate pesado sobre TimeSales (último trade de cada día), porque corre una vez. Idempotente por (ticker, fecha).
Se corre con `python -m scripts.backfill_cierre_canje [--desde 2024-01-01] [--dry]`.
Conecta con: lee `Trading.TimeSales`, escribe `Trading.CanjeCierre`; tickers desde `config.PARES_CANJE`.
