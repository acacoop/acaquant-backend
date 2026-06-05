Cron L-V a 20:35 UTC (17:35 ART, post-cierre del motor) que corre `jobs.cierre_canje`: materializa el cierre diario de los tickers de canje (CCL/MEP intra-bono, ej. AL30C/AL30D).

Conecta con: ejecuta `jobs/cierre_canje.py`; lee precios de cierre y escribe a `Trading.CanjeCierre`, leído por el service `api/services/canje.py`. Timeout 10m.
