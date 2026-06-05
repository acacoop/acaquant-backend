Variante z-score de la matriz de forwards: en vez del valor absoluto, colorea cada par por su desvío vs la historia (verde = forward descontado vs su norma, rojo = caro), usando media/desvío precalculados por par.

Conecta con: recibe la matriz live y los `stats` (media/desvío) del job `jobs.forwards_zscore` (vía backend de forwards, service `api.services.derivados`); convive con `web.cmp.forward-matrix` como modo alterno.
