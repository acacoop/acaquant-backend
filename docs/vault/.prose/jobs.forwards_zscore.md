Calcula los coeficientes (media y desvío muestral) de cada celda de la matriz de tasas forward, sobre los últimos 30 días hábiles de histórico. El z-score NO se persiste: el front lo computa en cada refresh con el live (`z = (forward_live − media) / desvío`), así el numerador se mueve cada 30s y el denominador queda fijo hasta el próximo cron. Omite pares con n_obs < 20 o desvío ~0.

Cron: 20:30 UTC (17:30 ART), post-cierre del motor de forwards. `--dry` solo imprime.

Conecta con: lee `Trading.ForwardsHistorico`, escribe `Trading.ForwardsZscore`. Lo consume la matriz de forwards en el front (que combina estos coeficientes con el live del motor).
