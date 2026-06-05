Panel de tasas forward por curva (tasa fija / CER) con tres modos: matriz live, gráfico de evolución temporal y z-scores. Pollea lento (5 min) para reflejar los coeficientes media/desvío que se recalculan 1 vez por día post-cierre.

Conecta con: lee forwards live + histórico + z-scores del backend (datos de `Trading.ForwardsHistorico` / `forwards_zscore`); incrusta `ForwardMatrix`, `ForwardMatrixZscore` e `InfoIcon`. Vive en la vista de Derivados/Estrategia.
