Cron L-V a 20:30 UTC (17:30 ART, post-cierre del motor) que corre `jobs.forwards_zscore`: calcula los coeficientes (media, desvío) por par de la matriz de forwards, base para el z-score que detecta forwards "caros/baratos".

Conecta con: ejecuta `jobs/forwards_zscore.py`; lee la historia de forwards (`Trading.ForwardsHistorico`) y persiste media/desvío por par. Timeout 10m.
